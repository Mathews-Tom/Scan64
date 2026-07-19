import pytest
from chess_lesson_spec import (
    AcceptedMove,
    Diagnosis,
    HighlightSquareCommand,
    Hint,
    Interaction,
    LessonSpec,
    Objective,
    Source,
    Verification,
)

from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson


def _create_valid_spec() -> LessonSpec:
    return LessonSpec(
        schema_version="0.1.0",
        lesson_id="test1",
        source=Source(
            kind="player_game",
            fen="r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 1 5",
        ),
        diagnosis=Diagnosis(primary="tactics.knight_fork", confidence=0.9),
        objective=Objective(type="exact_replay", instruction="Find move"),
        interaction=Interaction(
            input="move",
            maximum_attempts=3,
            accepted_moves=[AcceptedMove(san="Nc3", reason="best")],
        ),
        verification=Verification(status="unverified", engine="stockfish"),
    )


def test_valid_lesson() -> None:
    spec = _create_valid_spec()
    verify_lesson(spec)


def test_invalid_fen() -> None:
    spec = _create_valid_spec()
    spec.source.fen = "invalid_fen"
    with pytest.raises(LessonVerificationError, match="Invalid FEN"):
        verify_lesson(spec)


def test_illegal_move() -> None:
    spec = _create_valid_spec()
    spec.interaction.accepted_moves[0].san = "Kxe8"  # Illegal for white king here
    with pytest.raises(LessonVerificationError, match="is illegal"):
        verify_lesson(spec)


def test_invalid_visualization_square() -> None:
    spec = _create_valid_spec()
    spec.hints = [
        Hint(
            level=1,
            kind="visual",
            text="hint",
            visualizations=[HighlightSquareCommand(square="z9", description="Invalid square")],
        )
    ]
    with pytest.raises(LessonVerificationError, match="Invalid square"):
        verify_lesson(spec)
