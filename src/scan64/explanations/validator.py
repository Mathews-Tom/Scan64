from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import chess
from chess_lesson_spec import Explanation, ExplanationClaim, LessonSpec

from scan64.learning.evidence.models import Evidence
from scan64.providers.llm.adapters import LLMExplanationProvider
from scan64.providers.llm.contracts import ExplanationRequest, GeneratedExplanation


class GroundedExplanationValidationError(ValueError):
    """Raised when provider output exceeds verified explanation evidence."""


@dataclass(frozen=True)
class GroundedExplanationContext:
    """Verified facts and disclosure policy supplied to an explanation provider."""

    fen: str
    evidence: tuple[Evidence, ...]
    verified_lines: Mapping[str, tuple[tuple[str, ...], ...]]
    requested_hint_level: int
    maximum_certainty: Literal["observed", "likely", "certain"] = "observed"


_CERTAINTY_RANK = {"observed": 0, "likely": 1, "certain": 2}


def validate_generated_explanation(
    generated: GeneratedExplanation,
    context: GroundedExplanationContext,
) -> Explanation:
    """Validate provider claims before constructing a client-visible explanation."""

    if context.requested_hint_level < 1:
        raise GroundedExplanationValidationError("Requested hint level must be at least one")
    try:
        board = chess.Board(context.fen)
    except ValueError as error:
        raise GroundedExplanationValidationError(
            "Grounding context contains an invalid FEN"
        ) from error

    evidence_ids = _evidence_ids(context.evidence)
    for claim in generated.claims:
        _validate_claim(claim, board, context, evidence_ids)

    return Explanation(
        text=" ".join(claim.text for claim in generated.claims),
        claims=list(generated.claims),
    )

async def attach_validated_explanation(
    lesson: LessonSpec,
    provider: LLMExplanationProvider,
    request: ExplanationRequest,
    context: GroundedExplanationContext,
) -> None:
    """Attach provider output only after the full grounded-claim contract passes."""

    if lesson.source.fen != context.fen:
        raise GroundedExplanationValidationError(
            "Lesson source FEN and explanation grounding context must match"
        )
    generated = await provider.generate(request)
    lesson.explanation = validate_generated_explanation(generated, context)


def _evidence_ids(evidence: tuple[Evidence, ...]) -> set[str]:
    evidence_ids = {item.evidence_id for item in evidence}
    if len(evidence_ids) != len(evidence):
        raise GroundedExplanationValidationError(
            "Grounding context contains duplicate evidence IDs"
        )
    return evidence_ids


def _validate_claim(
    claim: ExplanationClaim,
    board: chess.Board,
    context: GroundedExplanationContext,
    evidence_ids: set[str],
) -> None:
    if not claim.evidence_ref.strip():
        raise GroundedExplanationValidationError(
            "Explanation claims require a non-empty evidence_ref"
        )
    if claim.evidence_ref not in evidence_ids:
        raise GroundedExplanationValidationError(
            f"Explanation claim references unknown evidence {claim.evidence_ref!r}"
        )
    if _CERTAINTY_RANK[claim.certainty] > _CERTAINTY_RANK[context.maximum_certainty]:
        raise GroundedExplanationValidationError("Explanation claim exceeds supported certainty")
    if claim.disclosure_level > context.requested_hint_level:
        raise GroundedExplanationValidationError(
            "Explanation claim exceeds the requested hint level"
        )
    _validate_line(
        claim.line,
        board,
        context.verified_lines.get(claim.evidence_ref, ()),
    )


def _validate_line(
    line: tuple[str, ...],
    board: chess.Board,
    verified_lines: tuple[tuple[str, ...], ...],
) -> None:
    if not line:
        return
    candidate = board.copy(stack=False)
    for move_uci in line:
        try:
            move = chess.Move.from_uci(move_uci)
        except ValueError as error:
            raise GroundedExplanationValidationError(
                f"Explanation claim contains malformed UCI move {move_uci!r}"
            ) from error
        if move not in candidate.legal_moves:
            raise GroundedExplanationValidationError(
                f"Explanation claim contains illegal move {move_uci!r}"
            )
        candidate.push(move)
    if not any(_is_prefix(line, verified_line) for verified_line in verified_lines):
        raise GroundedExplanationValidationError(
            "Explanation claim line does not match a verified variation"
        )


def _is_prefix(line: tuple[str, ...], verified_line: tuple[str, ...]) -> bool:
    return len(line) <= len(verified_line) and verified_line[: len(line)] == line
