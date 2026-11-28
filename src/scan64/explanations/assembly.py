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

import chess
import httpx
from chess_lesson_spec import Diagnosis, Explanation, ExplanationClaim

from scan64.explanations.facts import (
    NarratorFacts,
    NarratorFactsError,
    build_narrator_facts,
    verified_lines_by_evidence,
)
from scan64.explanations.phrase_bank import phrase_facts
from scan64.explanations.role_facts import RoleFact, RoleFactsError, numbered_san, role_facts
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
from scan64.explanations.voice_pipeline import SentenceWriter, VoicedExplanation, voice_facts
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
    sentences: int = 0
    sentence_fallbacks: int = 0
    facts_dropped: int = 0


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
    # Voice pipeline (#454): sentences written, and those that use the plain
    # fact text because every try failed the sentence check, and facts left
    # out because only one fact per role is voiced.
    sentences: int = 0
    sentence_fallbacks: int = 0
    facts_dropped: int = 0

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


def record_voiced_sentences(
    voiced: VoicedExplanation, pipeline: NarratorPipeline = "lesson"
) -> None:
    """Count one voiced explanation's sentences and its plain-fact fallbacks (#454)."""
    with _tallies_lock:
        _tallies[pipeline].sentences += len(voiced.sentences)
        _tallies[pipeline].sentence_fallbacks += voiced.fallback_sentences
        _tallies[pipeline].facts_dropped += voiced.dropped_facts
    if voiced.fallback_sentences:
        logger.info(
            "Narrator sentences fell back to the fact text: pipeline=%s count=%d of %d",
            pipeline,
            voiced.fallback_sentences,
            len(voiced.sentences),
            extra={
                "event": "narrator_sentence_fallback",
                "pipeline": pipeline,
                "count": voiced.fallback_sentences,
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
            pipeline: NarratorCounts(
                tally.attempts,
                tally.accepted,
                dict(tally.fallbacks),
                tally.sentences,
                tally.sentence_fallbacks,
                tally.facts_dropped,
            )
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


def sentence_writer(provider: LLMExplanationProvider) -> SentenceWriter | None:
    """``provider`` as a plain-sentence writer, or ``None`` when it can write only claims."""
    if isinstance(provider, LocalNarratorProvider):
        return provider if provider.supports_rewrite() else None
    return provider if isinstance(provider, SentenceWriter) else None


def lesson_role_facts(facts: NarratorFacts) -> tuple[RoleFact, ...] | None:
    """The role-labelled facts of the mistake (#460), or ``None`` without a best move."""
    position = facts.position
    best = position.best_move
    if best is None:
        return None
    played = position.played_move.uci
    best_line: tuple[str, ...] = next(
        (
            line.uci
            for line in facts.lines
            if not line.begins_with_played_move and line.uci and line.uci[0] == best.uci
        ),
        (best.uci,),
    )
    played_line: tuple[str, ...] = next(
        (
            line.uci[1:]
            for line in facts.lines
            if line.begins_with_played_move and line.uci and line.uci[0] == played
        ),
        (),
    )
    try:
        return role_facts(
            position.fen, played, best.uci, best_line=best_line, played_line=played_line
        )
    except RoleFactsError as error:
        logger.warning("Role facts unavailable: %s", error)
        return None


def voiced_explanation(
    voiced: VoicedExplanation, evidence_ref: str, line: Sequence[str] = ()
) -> Explanation:
    """A client-visible explanation: one claim per checked sentence."""
    claims = [
        ExplanationClaim(
            text=sentence.text, evidence_ref=evidence_ref, line=tuple(line), disclosure_level=1
        )
        for sentence in voiced.sentences
    ]
    return Explanation(text=voiced.text, claims=claims)


async def _attempt_voiced_generation(
    writer: SentenceWriter,
    facts: NarratorFacts,
    template_explanation: Explanation,
) -> Explanation | None:
    """The voice pipeline for one lesson (#454); ``None`` when it does not apply."""
    story = lesson_role_facts(facts)
    if not story:
        return None
    board = chess.Board(facts.position.fen)
    played = numbered_san(board, chess.Move.from_uci(facts.position.played_move.uci))
    try:
        voiced = await voice_facts(writer, story, mover=board.turn, played=played)
    except LLMProviderError:
        record_template_fallback("provider_error")
        return template_explanation
    record_voiced_sentences(voiced, "lesson")
    record_narrator_accepted("lesson")
    evidence_ref = (
        template_explanation.claims[0].evidence_ref
        if template_explanation.claims
        else facts.evidence[0].evidence_id
    )
    return voiced_explanation(voiced, evidence_ref)


def phrase_bank_explanation(
    diagnosis: Diagnosis,
    evidence: Sequence[Evidence],
    fen: str,
    template_explanation: Explanation,
) -> Explanation:
    """The explanation without a model: the phrase bank over the role facts (#461).

    The template stays the floor when the facts cannot be built or there is no
    better move to compare.
    """
    try:
        facts = build_narrator_facts(diagnosis, evidence, fen)
    except NarratorFactsError as error:
        logger.warning("Narrator facts unavailable: %s", error)
        return template_explanation
    story = lesson_role_facts(facts)
    if not story:
        return template_explanation
    voiced = phrase_facts(story)
    record_voiced_sentences(voiced, "lesson")
    evidence_ref = (
        template_explanation.claims[0].evidence_ref
        if template_explanation.claims
        else facts.evidence[0].evidence_id
    )
    return voiced_explanation(voiced, evidence_ref)


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
    writer = sentence_writer(provider)
    if writer is not None:
        voiced = await _attempt_voiced_generation(writer, facts, template_explanation)
        if voiced is not None:
            return voiced
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

    async with httpx.AsyncClient() as owned_client:
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
    floor every diagnosis falls back to. Without a model, the phrase bank words
    the role facts instead (#461). Explicit operator configuration takes
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
        phrase_bank_explanation(diagnosis, evidence, fen, template_explanation),
        attempt,
        local_provider=local_provider,
        environment=environment,
        client=client,
        voice=voice,
    )
