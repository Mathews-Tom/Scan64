from __future__ import annotations

from uuid import uuid4

import pytest
from chess_lesson_spec import LessonSpec

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson


def _lesson(accepted_move: str) -> LessonSpec:
    return LessonSpec.model_validate(
        {
            "schema_version": "1.0",
            "lesson_id": "objective-check",
            "source": {
                "kind": "player_game",
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            },
            "diagnosis": {"primary": "tactics.fork", "confidence": 1.0},
            "objective": {"type": "find_best_move", "instruction": "Find the best move."},
            "interaction": {
                "input": "move",
                "maximum_attempts": 1,
                "accepted_moves": [{"san": accepted_move}],
            },
            "verification": {"status": "unverified", "engine": "Stockfish"},
        }
    )


def _analysis() -> EngineAnalysis:
    return EngineAnalysis(position_id=uuid4(), raw_result=[{"pv": ["e2e4", "e7e5"]}])


def test_verification_rejects_accepted_move_that_is_not_engine_best() -> None:
    with pytest.raises(LessonVerificationError, match="does not match"):
        verify_lesson(_lesson("d4"), _analysis())


def test_verification_marks_an_engine_confirmed_lesson_verified() -> None:
    lesson = _lesson("e4")

    verify_lesson(lesson, _analysis())

    assert lesson.verification.status == "verified"


def test_verification_rejects_a_lesson_without_accepted_moves() -> None:
    lesson = _lesson("e4")
    lesson.interaction.accepted_moves = []

    with pytest.raises(LessonVerificationError, match="at least one accepted move"):
        verify_lesson(lesson, _analysis())
