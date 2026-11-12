import json
from pathlib import Path
from uuid import uuid4

import chess
import pytest
from chess_lesson_spec import AcceptedMove, Diagnosis

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.exercises.sound_moves import analyze_lesson_source, sound_moves
from scan64.learning.tolerance import SOUND_MOVE_WIN_PROBABILITY_LOSS, win_probability
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson


@pytest.mark.asyncio
async def test_recorded_multipv_accepts_two_sound_moves_and_rejects_played_move() -> None:
    recorded = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "m47_source_multipv.json").read_text()
    )
    calls: list[tuple[str, int]] = []

    class RecordedAdapter:
        async def analyze_position(
            self,
            fen: str,
            nodes: int | None = None,
            depth: int | None = None,
            time_ms: int | None = None,
            multipv: int = 1,
        ) -> EngineAnalysis:
            calls.append((fen, multipv))
            return EngineAnalysis(position_id=uuid4(), raw_result=recorded["raw_result"][:multipv])

    analysis = await analyze_lesson_source(RecordedAdapter(), recorded["fen"])
    assert calls == [(recorded["fen"], 2), (recorded["fen"], 4)]
    board = chess.Board(recorded["fen"])
    accepted = [board.san(move) for move in sound_moves(board, analysis)]
    assert accepted == ["e4", "d4"]
    lesson = await generate_exact_replay_exercise(
        Diagnosis(primary="tactics.fork", confidence=1.0),
        recorded["fen"],
        "sound-lesson",
        accepted,
    )
    verify_lesson(lesson, analysis)
    assert [move.san for move in lesson.interaction.accepted_moves] == ["e4", "d4"]
    lesson.interaction.accepted_moves.append(AcceptedMove(san=recorded["played_move"]))
    with pytest.raises(LessonVerificationError, match="engine-confirmed sound"):
        verify_lesson(lesson, analysis)


@pytest.mark.asyncio
async def test_source_search_widens_to_eight_when_all_lines_are_sound() -> None:
    calls: list[int] = []

    class EqualAdapter:
        async def analyze_position(
            self,
            fen: str,
            nodes: int | None = None,
            depth: int | None = None,
            time_ms: int | None = None,
            multipv: int = 1,
        ) -> EngineAnalysis:
            calls.append(multipv)
            return EngineAnalysis(
                position_id=uuid4(),
                raw_result=[
                    {"pv": [move.uci()], "score_cp": 100}
                    for move in list(chess.Board(fen).legal_moves)[:multipv]
                ],
            )

    analysis = await analyze_lesson_source(EqualAdapter(), chess.STARTING_FEN)
    assert calls == [2, 4, 8]
    assert len(sound_moves(chess.Board(), analysis)) == 8


def test_probability_policy_uses_mates_and_side_to_move_scores() -> None:
    assert SOUND_MOVE_WIN_PROBABILITY_LOSS == 0.08
    assert win_probability({"score_cp": 0}) == 0.5
    assert win_probability({"score_mate": 2}) == 1.0
    assert win_probability({"score_mate": -2}) == 0.0
    board = chess.Board()
    board.push_uci("e2e4")
    analysis = EngineAnalysis(
        position_id=uuid4(),
        raw_result=[
            {"pv": ["e7e5"], "score_cp": 100},
            {"pv": ["d7d5"], "score_cp": 50},
            {"pv": ["a7a6"], "score_cp": -300},
        ],
    )
    assert [board.san(move) for move in sound_moves(board, analysis)] == ["e5", "d5"]


def test_unscored_legacy_pv_proves_only_its_first_move() -> None:
    analysis = EngineAnalysis(position_id=uuid4(), raw_result=[{"pv": ["e2e4"]}, {"pv": ["d2d4"]}])
    assert sound_moves(chess.Board(), analysis) == [chess.Move.from_uci("e2e4")]
