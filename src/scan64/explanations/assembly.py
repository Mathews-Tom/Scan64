"""Resolve the client-visible explanation for a diagnosis.

The template floor (``explanations/templates/provider.py``) is always
computed and is the default explanation. When an operator has configured an
LLM provider (``SCAN64_LLM_CONFIG``, off by default), its output is routed
through ``explanations/validator.py`` and only trusted if every claim is
grounded in the diagnosis's own evidence; a rejected (ungrounded) generation
falls back to the template rather than surfacing an error or a partial
explanation. Rejection is expected behaviour, not a failure to suppress: each
fallback is logged with its reason code (never evidence contents) and counted
in-process, so the fallback-to-template rate is visible via
``template_fallback_counts``.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

import httpx
from chess_lesson_spec import Diagnosis, Explanation

from scan64.explanations.facts import (
    SCHEMA_VERSION,
    NarratorFacts,
    NarratorFactsError,
    build_narrator_facts,
    verified_lines_by_evidence,
)
from scan64.explanations.settings import OPERATOR_LLM_CONFIG
from scan64.explanations.slots import lesson_slot_catalogue
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.explanations.validator import (
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    RejectionReason,
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest, LLMMessage, LLMProviderError
from scan64.providers.llm.adapters import LLMExplanationProvider
from scan64.providers.llm.config import LLMProviderConfig, create_llm_provider

logger = logging.getLogger(__name__)

TemplateFallbackReason = RejectionReason | Literal["provider_error"]

_template_fallbacks: Counter[str] = Counter()
_template_fallbacks_lock = threading.Lock()


def record_template_fallback(reason: TemplateFallbackReason) -> None:
    """Count and log one template fallback by reason code (never evidence)."""
    with _template_fallbacks_lock:
        _template_fallbacks[reason] += 1
    logger.info(
        "Explanation generation fell back to the template: reason=%s",
        reason,
        extra={"event": "explanation_template_fallback", "reason": reason},
    )


def template_fallback_counts() -> dict[str, int]:
    """Template fallbacks by reason code since process start."""
    with _template_fallbacks_lock:
        return dict(_template_fallbacks)


# The explanation is displayed immediately, not gated behind a progressive
# hint ladder, so any claim's disclosure_level is acceptable.
_FULL_DISCLOSURE_HINT_LEVEL = 3


def _grounding_context(
    facts: NarratorFacts, evidence: Sequence[Evidence], fen: str
) -> GroundedExplanationContext:
    referenced_ids = {item.evidence_id for item in facts.evidence}
    return GroundedExplanationContext(
        fen=fen,
        evidence=tuple(item for item in evidence if item.evidence_id in referenced_ids),
        verified_lines=verified_lines_by_evidence(facts),
        requested_hint_level=_FULL_DISCLOSURE_HINT_LEVEL,
        slots=lesson_slot_catalogue(facts),
    )


def grounding_context_for(
    diagnosis: Diagnosis, evidence: Sequence[Evidence], fen: str
) -> GroundedExplanationContext:
    """The grounding context ``resolve_explanation`` checks a generation against.

    Public for tools that score generations outside the pipeline, such as the
    narrator benchmark. Raises ``NarratorFactsError`` like the pipeline's own
    facts step.
    """
    return _grounding_context(build_narrator_facts(diagnosis, evidence, fen), evidence, fen)


def slot_rules(names: str, example: str) -> str:
    """The slot instructions NARRATOR_SYSTEM_PROMPT and BOARD_NARRATOR_SYSTEM_PROMPT share."""
    return (
        "Write every chess fact only through a slot from the slot list (the last user "
        f"message): put the slot name in braces, for example {names}, and code puts the "
        "slot's value in its place. Copy slot names exactly; use only slots listed for the "
        "evidence item the claim cites. Outside the slots write plain words only: no moves, "
        "squares, piece names, colour words, numbers or evaluations, and none of the words "
        "check, mate, capture, take, trade, threat, attack, defend, hang, material, win, lose, "
        "gain, ahead, behind or a tactic name. A slot value is a whole fact; do not add a "
        "chess fact around it, and do not say which side gains from it unless a slot says so. "
        f'Example claim text: "{example}"'
    )


NARRATOR_SYSTEM_PROMPT = (
    "You explain one chess mistake to the learner who made it. The first user message is a "
    f"JSON document following the Scan64 schema {SCHEMA_VERSION} "
    "(schemas/narrator-facts.schema.json). The board library and the chess engine computed "
    "every fact in it; a field left out has its schema default (no capture, no check, "
    "no threats). Use only those facts: do not work out moves, captures, checks, "
    "threats, material or evaluations yourself, and state nothing the facts do not state.\n"
    "- position: the learner is side_to_move; played_move is the mistake; best_move is the "
    "engine's choice; mistake_class names the mistake.\n"
    "- lines: engine lines from the position before the mistake. A line with "
    "begins_with_played_move=true starts with the learner's mistake.\n"
    "- "
    + slot_rules(
        "{played} or {line:L1}", "You played {played}, but {best} was stronger: {capture:best}."
    )
    + "\n"
    "Answer with claims. Each claim: text (one or two plain sentences with slots), "
    "evidence_ref (an evidence_id from evidence), line (a JSON list of UCI move strings "
    "copied from the start of the uci list of a line whose line_id is in that evidence "
    'item\'s line_ids, or []; never a line_id), certainty "observed", disclosure_level 1. '
    'Call the learner "you" and the other side "your opponent".'
)


def _explanation_request(facts: NarratorFacts) -> ExplanationRequest:
    return ExplanationRequest(
        messages=(
            LLMMessage(role="system", content=NARRATOR_SYSTEM_PROMPT),
            LLMMessage(role="user", content=facts.to_prompt_json()),
            LLMMessage(role="user", content=lesson_slot_catalogue(facts).prompt()),
        )
    )


async def _attempt_grounded_generation(
    provider: LLMExplanationProvider,
    diagnosis: Diagnosis,
    evidence: Sequence[Evidence],
    fen: str,
    template_explanation: Explanation,
) -> Explanation:
    try:
        facts = build_narrator_facts(diagnosis, evidence, fen)
    except NarratorFactsError as error:
        logger.warning("Narrator facts unavailable: %s", error)
        record_template_fallback("invalid_evidence")
        return template_explanation
    context = _grounding_context(facts, evidence, fen)
    request = _explanation_request(facts)
    try:
        generated = await provider.generate(request)
        return validate_generated_explanation(generated, context)
    except GroundedExplanationValidationError as error:
        record_template_fallback(error.reason)
        return template_explanation
    except LLMProviderError:
        record_template_fallback("provider_error")
        return template_explanation


async def run_with_explanation_provider[T](
    template_result: T,
    attempt: Callable[[LLMExplanationProvider], Awaitable[T]],
    *,
    local_provider: LLMExplanationProvider | None,
    environment: Mapping[str, str] | None,
    client: httpx.AsyncClient | None,
) -> T:
    """Run ``attempt`` with the configured provider, else return ``template_result``.

    Explicit operator configuration (``SCAN64_LLM_CONFIG``) takes precedence over
    the owner's installation-local model; with neither, or a ``template``
    operator config, the template result is returned without any provider call.
    """
    environment_values = os.environ if environment is None else environment
    raw_path = environment_values.get(OPERATOR_LLM_CONFIG)
    if raw_path is None:
        if local_provider is not None:
            return await attempt(local_provider)
        return template_result

    config = LLMProviderConfig.from_toml(Path(raw_path))
    if config.provider == "template":
        return template_result

    if client is not None:
        provider = create_llm_provider(config, client=client, environment=environment_values)
        if provider is None:
            return template_result
        return await attempt(provider)

    async with httpx.AsyncClient() as owned_client:
        provider = create_llm_provider(config, client=owned_client, environment=environment_values)
        if provider is None:
            return template_result
        return await attempt(provider)


async def resolve_explanation(
    diagnosis: Diagnosis,
    evidence: Sequence[Evidence],
    fen: str,
    *,
    local_provider: LLMExplanationProvider | None = None,
    environment: Mapping[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> Explanation:
    """Resolve the client-visible explanation for a diagnosis.

    Always computes the deterministic template explanation first -- it is the
    floor every diagnosis falls back to. Explicit operator configuration takes
    precedence over the owner's installation-local model. Both are subject to
    the same grounding check; only a grounded response replaces the template.

    ``client`` lets a caller (tests, or a host that already owns a shared
    client) supply one; when omitted, a client is opened and closed for the
    duration of this call.
    """
    template_explanation = await TemplateExplanationProvider().explain(diagnosis, evidence)

    async def attempt(provider: LLMExplanationProvider) -> Explanation:
        return await _attempt_grounded_generation(
            provider, diagnosis, evidence, fen, template_explanation
        )

    return await run_with_explanation_provider(
        template_explanation,
        attempt,
        local_provider=local_provider,
        environment=environment,
        client=client,
    )
