"""Resolve the client-visible explanation for a diagnosis.

The template floor (``explanations/templates/provider.py``) is always
computed and is the default explanation. When an operator has configured an
LLM provider (``SCAN64_LLM_CONFIG``, off by default), its output is routed
through ``explanations/validator.py`` and only trusted if every claim is
grounded in the diagnosis's own evidence; a rejected (ungrounded) generation
falls back to the template rather than surfacing an error or a partial
explanation. Rejection is expected behaviour, not a failure to suppress: each
fallback is logged with its reason code (never evidence contents) and counted
in-process. Attempts and accepted generations are counted too, per pipeline
(lessons and Analysis Board lines), so the fallback-to-template rate is visible
via ``narrator_counts`` and ``GET /v1/diagnostics/narrator``.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
from chess_lesson_spec import Diagnosis, Explanation

from scan64.explanations.facts import (
    NarratorFacts,
    NarratorFactsError,
    build_narrator_facts,
    verified_lines_by_evidence,
)
from scan64.explanations.settings import OPERATOR_LLM_CONFIG, LocalNarratorProvider
from scan64.explanations.slots import lesson_slot_catalogue
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.explanations.validator import (
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    RejectionReason,
    validate_generated_explanation,
)
from scan64.explanations.voice import DEFAULT_NARRATOR_VOICE, NarratorVoice, with_voice
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest, LLMMessage, LLMProviderError
from scan64.providers.llm.adapters import LLMExplanationProvider
from scan64.providers.llm.config import LLMProviderConfig, create_llm_provider

logger = logging.getLogger(__name__)

TemplateFallbackReason = RejectionReason | Literal["provider_error"]

NarratorPipeline = Literal["lesson", "board"]
NARRATOR_PIPELINES: tuple[NarratorPipeline, ...] = ("lesson", "board")


@dataclass
class _PipelineTally:
    attempts: int = 0
    accepted: int = 0
    fallbacks: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class NarratorCounts:
    """One pipeline's narrator outcomes since process start (counts only, no evidence).

    ``attempts`` counts every provider attempt. Each finished attempt adds one
    to ``accepted`` or to one fallback reason, so the two sum to ``attempts``
    except for attempts still in flight or ended by an unexpected exception.
    """

    attempts: int
    accepted: int
    fallbacks: dict[str, int]

    @property
    def fallback_total(self) -> int:
        return sum(self.fallbacks.values())

    @property
    def fallback_rate(self) -> float | None:
        """Fallbacks per attempt; ``None`` before the first attempt."""
        return self.fallback_total / self.attempts if self.attempts else None


_tallies: dict[NarratorPipeline, _PipelineTally] = {
    pipeline: _PipelineTally() for pipeline in NARRATOR_PIPELINES
}
_tallies_lock = threading.Lock()


def record_narrator_attempt(pipeline: NarratorPipeline = "lesson") -> None:
    """Count one narrator attempt: a configured provider is about to be used."""
    with _tallies_lock:
        _tallies[pipeline].attempts += 1


def record_narrator_accepted(pipeline: NarratorPipeline = "lesson") -> None:
    """Count one generation that passed validation and replaced the template."""
    with _tallies_lock:
        _tallies[pipeline].accepted += 1


def record_template_fallback(
    reason: TemplateFallbackReason, pipeline: NarratorPipeline = "lesson"
) -> None:
    """Count and log one template fallback by reason code (never evidence)."""
    with _tallies_lock:
        _tallies[pipeline].fallbacks[reason] += 1
    logger.info(
        "Explanation generation fell back to the template: pipeline=%s reason=%s",
        pipeline,
        reason,
        extra={
            "event": "explanation_template_fallback",
            "pipeline": pipeline,
            "reason": reason,
        },
    )


def template_fallback_counts() -> dict[str, int]:
    """Template fallbacks by reason code since process start, summed over pipelines."""
    total: Counter[str] = Counter()
    with _tallies_lock:
        for tally in _tallies.values():
            total.update(tally.fallbacks)
    return dict(total)


def narrator_counts() -> dict[NarratorPipeline, NarratorCounts]:
    """A consistent snapshot of every pipeline's counts since process start."""
    with _tallies_lock:
        return {
            pipeline: NarratorCounts(tally.attempts, tally.accepted, dict(tally.fallbacks))
            for pipeline, tally in _tallies.items()
        }


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


# Prompt budget (#408): the longest narrator prompt stays under this many
# tokens. The estimate divides characters by PROMPT_CHARS_PER_TOKEN, set below
# the lowest ratio measured with gemma4-12b, qwen3.5-9b and granite4.2-8b
# (2.58, prompt_eval_count including the chat template).
PROMPT_TOKEN_TARGET = 2000
PROMPT_CHARS_PER_TOKEN = 2.4


def estimated_prompt_tokens(request: ExplanationRequest) -> int:
    """An upper estimate of a request's prompt tokens (characters / PROMPT_CHARS_PER_TOKEN)."""
    characters = sum(len(message.content) for message in request.messages)
    return -(-characters * 10 // int(PROMPT_CHARS_PER_TOKEN * 10))


def slot_rules(names: str, example: str) -> str:
    """The slot instructions NARRATOR_SYSTEM_PROMPT and BOARD_NARRATOR_SYSTEM_PROMPT share."""
    return (
        "Write every chess fact as a slot from the slot list: the slot name in braces, for "
        f"example {names}. Code puts the slot's value in its place. Copy slot names exactly. "
        "A slot value is a whole fact: add no chess fact around it.\n"
        "- Outside slots write plain words: no moves, squares, piece names, colours, numbers "
        "or evaluations. Use a chess word only if the slot list allows it as a plain word, "
        "never in braces.\n"
        "- Name a cause only through a {factor:...} slot. With no factor slot, name no "
        "cause.\n"
        f"- Use only slot names the slot list holds. {example}"
    )


NARRATOR_SYSTEM_PROMPT = (
    "You explain one chess mistake to the learner who made it. Code computed every fact "
    "with the board library, the chess engine and the factor module; you only choose plain "
    "words around them.\n"
    "- "
    + slot_rules(
        "{played} or {line:L1}",
        "The slot list ends with an example claim text.",
    )
    + "\n"
    "Answer with claims. Each claim: text (one or two plain sentences with slots), "
    "evidence_ref (the quoted evidence_ref of an evidence item, never a line id), line [], "
    'certainty "observed", disclosure_level 1. Call the learner "you" and the other side '
    '"your opponent".'
)


def narrator_system_prompt(voice: NarratorVoice = DEFAULT_NARRATOR_VOICE) -> str:
    """NARRATOR_SYSTEM_PROMPT with the voice fragment as its last line (#387)."""
    return with_voice(NARRATOR_SYSTEM_PROMPT, voice)


def _explanation_request(
    facts: NarratorFacts, voice: NarratorVoice = DEFAULT_NARRATOR_VOICE
) -> ExplanationRequest:
    return ExplanationRequest(
        messages=(
            LLMMessage(role="system", content=narrator_system_prompt(voice)),
            LLMMessage(role="user", content=lesson_slot_catalogue(facts).prompt()),
        )
    )


async def _attempt_grounded_generation(
    provider: LLMExplanationProvider,
    diagnosis: Diagnosis,
    evidence: Sequence[Evidence],
    fen: str,
    template_explanation: Explanation,
    voice: NarratorVoice = DEFAULT_NARRATOR_VOICE,
) -> Explanation:
    record_narrator_attempt("lesson")
    try:
        facts = build_narrator_facts(diagnosis, evidence, fen)
    except NarratorFactsError as error:
        logger.warning("Narrator facts unavailable: %s", error)
        record_template_fallback("invalid_evidence")
        return template_explanation
    context = _grounding_context(facts, evidence, fen)
    request = _explanation_request(facts, voice)
    try:
        generated = await provider.generate(request)
        explanation = validate_generated_explanation(generated, context)
    except GroundedExplanationValidationError as error:
        record_template_fallback(error.reason)
        return template_explanation
    except LLMProviderError:
        record_template_fallback("provider_error")
        return template_explanation
    record_narrator_accepted("lesson")
    return explanation


async def run_with_explanation_provider[T](
    template_result: T,
    attempt: Callable[[LLMExplanationProvider, NarratorVoice], Awaitable[T]],
    *,
    local_provider: LLMExplanationProvider | None,
    environment: Mapping[str, str] | None,
    client: httpx.AsyncClient | None,
    voice: NarratorVoice | None = None,
) -> T:
    """Run ``attempt`` with the configured provider and voice, else return ``template_result``.

    Explicit operator configuration (``SCAN64_LLM_CONFIG``) takes precedence over
    the owner's installation-local model; with neither, or a ``template``
    operator config, the template result is returned without any provider call.

    The voice is ``voice`` when given (benchmark, tests), else the operator
    config's ``llm.voice``, else the voice saved with the local grant
    (``LocalNarratorProvider``), else ``DEFAULT_NARRATOR_VOICE``.
    """
    environment_values = os.environ if environment is None else environment
    raw_path = environment_values.get(OPERATOR_LLM_CONFIG)
    if raw_path is None:
        if local_provider is not None:
            local_voice = (
                local_provider.voice
                if isinstance(local_provider, LocalNarratorProvider)
                else DEFAULT_NARRATOR_VOICE
            )
            return await attempt(local_provider, voice or local_voice)
        return template_result

    config = LLMProviderConfig.from_toml(Path(raw_path))
    if config.provider == "template":
        return template_result
    operator_voice = voice or config.voice

    if client is not None:
        provider = create_llm_provider(config, client=client, environment=environment_values)
        if provider is None:
            return template_result
        return await attempt(provider, operator_voice)

    async with httpx.AsyncClient(timeout=config.timeout_seconds) as owned_client:
        provider = create_llm_provider(config, client=owned_client, environment=environment_values)
        if provider is None:
            return template_result
        return await attempt(provider, operator_voice)


async def resolve_explanation(
    diagnosis: Diagnosis,
    evidence: Sequence[Evidence],
    fen: str,
    *,
    local_provider: LLMExplanationProvider | None = None,
    environment: Mapping[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    voice: NarratorVoice | None = None,
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

    async def attempt(provider: LLMExplanationProvider, chosen: NarratorVoice) -> Explanation:
        return await _attempt_grounded_generation(
            provider, diagnosis, evidence, fen, template_explanation, chosen
        )

    return await run_with_explanation_provider(
        template_explanation,
        attempt,
        local_provider=local_provider,
        environment=environment,
        client=client,
        voice=voice,
    )
