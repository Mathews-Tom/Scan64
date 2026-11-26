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
from collections.abc import Mapping, Sequence
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


def _record_template_fallback(reason: TemplateFallbackReason) -> None:
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
    )


NARRATOR_SYSTEM_PROMPT = (
    "You explain one chess mistake to the learner who made it. The user message is a JSON "
    f"document following the Scan64 schema {SCHEMA_VERSION} "
    "(schemas/narrator-facts.schema.json). The board library and the chess engine computed "
    "every fact in it; a field left out has its schema default (no capture, no check, "
    "no threats). Use only those facts: do not work out moves, captures, checks, "
    "threats, material or evaluations yourself, and state nothing the JSON does not state.\n"
    "- position: the learner is side_to_move; played_move is the mistake; best_move is the "
    "engine's choice; mistake_class names the mistake.\n"
    "- Evaluations are from the learner's point of view in centipawns (100 = one pawn). "
    "Never state an evaluation whose status is unknown.\n"
    "- lines: engine lines from the position before the mistake; each move lists its SAN, "
    "piece, captures, check, checkmate, material_after, new_threats and evaluation_after. A "
    "line with begins_with_played_move=true starts with the learner's mistake.\n"
    "- Name moves in SAN exactly as given. Name only pieces and squares that appear in the "
    "facts.\n"
    "Answer with claims. Each claim: text (one or two plain sentences), evidence_ref (an "
    "evidence_id from evidence), line (a JSON list of UCI move strings copied from the start "
    "of the uci list of a line whose line_id is in that evidence item's line_ids, or []; "
    'never a line_id), certainty "observed", disclosure_level 1. Name sides as '
    '"the learner", "the opponent", "White to move" or "White plays ..."; never use a bare '
    "colour word as an adjective."
)


def _explanation_request(facts: NarratorFacts) -> ExplanationRequest:
    return ExplanationRequest(
        messages=(
            LLMMessage(role="system", content=NARRATOR_SYSTEM_PROMPT),
            LLMMessage(role="user", content=facts.to_prompt_json()),
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
        _record_template_fallback("invalid_evidence")
        return template_explanation
    context = _grounding_context(facts, evidence, fen)
    request = _explanation_request(facts)
    try:
        generated = await provider.generate(request)
        return validate_generated_explanation(generated, context)
    except GroundedExplanationValidationError as error:
        _record_template_fallback(error.reason)
        return template_explanation
    except LLMProviderError:
        _record_template_fallback("provider_error")
        return template_explanation


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

    environment_values = os.environ if environment is None else environment
    raw_path = environment_values.get(OPERATOR_LLM_CONFIG)
    if raw_path is None:
        if local_provider is not None:
            return await _attempt_grounded_generation(
                local_provider,
                diagnosis,
                evidence,
                fen,
                template_explanation,
            )
        return template_explanation

    config = LLMProviderConfig.from_toml(Path(raw_path))
    if config.provider == "template":
        return template_explanation

    if client is not None:
        provider = create_llm_provider(config, client=client, environment=environment_values)
        if provider is None:
            return template_explanation
        return await _attempt_grounded_generation(
            provider, diagnosis, evidence, fen, template_explanation
        )

    async with httpx.AsyncClient() as owned_client:
        provider = create_llm_provider(config, client=owned_client, environment=environment_values)
        if provider is None:
            return template_explanation
        return await _attempt_grounded_generation(
            provider, diagnosis, evidence, fen, template_explanation
        )
