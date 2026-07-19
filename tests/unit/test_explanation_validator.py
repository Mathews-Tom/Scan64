from __future__ import annotations

from typing import Literal

import pytest
from chess_lesson_spec import Diagnosis, ExplanationClaim, LessonSpec

from scan64.explanations.validator import (
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    attach_validated_explanation,
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.providers.llm.contracts import (
    ExplanationRequest,
    GeneratedExplanation,
    LLMMessage,
)

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="ev_1",
        kind="engine_line",
        position_id="position_1",
        engine_analysis_id="analysis_1",
        claim="e4 is a verified candidate move",
    )


class ControlledLLMProvider:
    """Returns a predetermined LLM response for validator boundary tests."""

    def __init__(self, response: GeneratedExplanation) -> None:
        self._response = response

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        return self._response


def _context(
    *,
    requested_hint_level: int = 1,
    maximum_certainty: Literal["observed", "likely", "certain"] = "observed",
) -> GroundedExplanationContext:
    return GroundedExplanationContext(
        fen=_STARTING_FEN,
        evidence=(_evidence(),),
        verified_lines={"ev_1": (("e2e4", "e7e5"),)},
        requested_hint_level=requested_hint_level,
        maximum_certainty=maximum_certainty,
    )


def _request() -> ExplanationRequest:
    return ExplanationRequest(
        messages=(LLMMessage(role="user", content="Explain verified evidence."),)
    )


def _generated(
    *,
    evidence_ref: str = "ev_1",
    line: tuple[str, ...] = ("e2e4",),
    certainty: Literal["observed", "likely", "certain"] = "observed",
    disclosure_level: int = 1,
) -> GeneratedExplanation:
    return GeneratedExplanation(
        claims=(
            ExplanationClaim(
                text="The verified move develops central control.",
                evidence_ref=evidence_ref,
                line=line,
                certainty=certainty,
                disclosure_level=disclosure_level,
            ),
        ),
    )


async def _lesson() -> LessonSpec:
    return await generate_exact_replay_exercise(
        diagnosis=Diagnosis(
            primary="tactics.knight_fork",
            confidence=0.9,
            evidence_refs=["ev_1"],
        ),
        fen=_STARTING_FEN,
        lesson_id="les_validator_boundary",
        best_move_san="e4",
    )


def test_validator_accepts_evidence_referenced_verified_claim() -> None:
    generated = GeneratedExplanation(
        claims=(
            ExplanationClaim(
                text="The verified move develops central control.",
                evidence_ref="ev_1",
                line=("e2e4",),
                certainty="observed",
                disclosure_level=1,
            ),
        ),
    )
    context = GroundedExplanationContext(
        fen=_STARTING_FEN,
        evidence=(_evidence(),),
        verified_lines={"ev_1": (("e2e4", "e7e5"),)},
        requested_hint_level=1,
    )

    explanation = validate_generated_explanation(generated, context)

    assert explanation.text == "The verified move develops central control."
    assert explanation.claims[0].evidence_ref == "ev_1"


@pytest.mark.asyncio
async def test_illegal_mocked_move_is_rejected_before_lesson_attachment() -> None:
    lesson = await _lesson()
    provider = ControlledLLMProvider(_generated(line=("e2e5",)))

    with pytest.raises(GroundedExplanationValidationError, match="illegal move"):
        await attach_validated_explanation(lesson, provider, _request(), _context())

    assert lesson.explanation is None


@pytest.mark.asyncio
async def test_over_disclosing_mocked_response_is_rejected_before_lesson_attachment() -> None:
    lesson = await _lesson()
    provider = ControlledLLMProvider(_generated(disclosure_level=2))

    with pytest.raises(GroundedExplanationValidationError, match="requested hint level"):
        await attach_validated_explanation(lesson, provider, _request(), _context())

    assert lesson.explanation is None


@pytest.mark.asyncio
async def test_unsupported_certainty_is_rejected_before_lesson_attachment() -> None:
    lesson = await _lesson()
    provider = ControlledLLMProvider(_generated(certainty="certain"))

    with pytest.raises(GroundedExplanationValidationError, match="supported certainty"):
        await attach_validated_explanation(lesson, provider, _request(), _context())

    assert lesson.explanation is None


@pytest.mark.asyncio
async def test_valid_mocked_response_attaches_grounded_explanation() -> None:
    lesson = await _lesson()

    await attach_validated_explanation(
        lesson,
        ControlledLLMProvider(_generated()),
        _request(),
        _context(),
    )

    assert lesson.explanation is not None
    assert lesson.explanation.text == "The verified move develops central control."
    assert lesson.explanation.claims[0].evidence_ref == "ev_1"


@pytest.mark.asyncio
async def test_legal_but_unverified_mocked_line_is_rejected_before_lesson_attachment() -> None:
    lesson = await _lesson()
    provider = ControlledLLMProvider(_generated(line=("g1f3",)))

    with pytest.raises(GroundedExplanationValidationError, match="verified variation"):
        await attach_validated_explanation(lesson, provider, _request(), _context())

    assert lesson.explanation is None


def test_line_verified_by_different_evidence_is_rejected() -> None:
    other_evidence = Evidence(
        evidence_id="ev_2",
        kind="engine_line",
        position_id="position_1",
        engine_analysis_id="analysis_2",
        claim="Nf3 is a verified candidate move",
    )
    context = GroundedExplanationContext(
        fen=_STARTING_FEN,
        evidence=(_evidence(), other_evidence),
        verified_lines={
            "ev_1": (("e2e4", "e7e5"),),
            "ev_2": (("g1f3",),),
        },
        requested_hint_level=1,
    )

    with pytest.raises(GroundedExplanationValidationError, match="verified variation"):
        validate_generated_explanation(_generated(line=("g1f3",)), context)


def test_unknown_evidence_reference_is_rejected() -> None:
    with pytest.raises(GroundedExplanationValidationError, match="unknown evidence"):
        validate_generated_explanation(_generated(evidence_ref="ev_missing"), _context())
