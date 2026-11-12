import json
from pathlib import Path

import chess
import pytest
from chess_lesson_spec import Diagnosis, Explanation

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.exercises.hints import fill_lesson_hints
from scan64.learning.insights import insight_step, position_insight
from scan64.learning.verification.verifier import verify_lesson

RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/hint_engine_recording.json").read_text()
)


@pytest.mark.parametrize("name", ["A", "C", "D"])
def test_early_steps_exclude_every_candidate_move_and_destination(name):
    record = RECORDING["positions"][name]
    board = chess.Board(record["fen"])
    insight = position_insight(board, EngineAnalysis(raw_result=record["raw_result"]))
    for level in (1, 2):
        step = insight_step(board, insight, level)
        serialized = step.model_dump_json()
        for candidate in insight.candidates:
            assert candidate.move not in serialized
            assert candidate.san not in serialized
            assert candidate.move[2:4] not in serialized
        assert "candidates" not in serialized
        assert "line" not in step.model_dump()
        if level == 1:
            assert len(step.visualizations) == 1
            assert step.text.endswith("?")
        else:
            assert step.step == 2
            assert step.piece == chess.piece_name(
                board.piece_type_at(chess.Move.from_uci(insight.candidates[0].move).from_square)
            )
    assert insight_step(board, insight, 3) is insight


@pytest.mark.parametrize(
    ("fen", "move"),
    [
        ("4k3/8/8/8/4n3/8/4R3/4K3 w - - 0 1", "e2e4"),
        (chess.STARTING_FEN, "e2e4"),
        (chess.STARTING_FEN, "g1f3"),
        (RECORDING["positions"]["C"]["fen"], "e1c1"),
    ],
)
def test_capture_pawn_development_and_castling_do_not_leak_destinations(fen, move):
    board = chess.Board(fen)
    insight = position_insight(board, EngineAnalysis(raw_result=[{"pv": [move], "score_cp": 0}]))
    for level in (1, 2):
        serialized = insight_step(board, insight, level).model_dump_json()
        assert move not in serialized
        assert board.san(chess.Move.from_uci(move)) not in serialized
        assert move[2:4] not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["A", "C", "D"])
async def test_lesson_ladder_uses_only_accepted_source_lines_and_is_idempotent(name):
    record = RECORDING["positions"][name]
    board = chess.Board(record["fen"])
    analysis = EngineAnalysis(raw_result=record["raw_result"])
    full = position_insight(board, analysis)
    # Exercise a non-best accepted alternative as well as the only sound tactic.
    candidate = full.candidates[-1]
    lesson = await generate_exact_replay_exercise(
        Diagnosis(primary="test", confidence=1), board.fen(), "ladder", [candidate.san]
    )
    lesson.explanation = Explanation(text="Existing validated explanation.")
    fill_lesson_hints(lesson, analysis)
    verify_lesson(lesson, analysis)
    assert [hint.level for hint in lesson.hints] == [1, 2, 3]
    assert lesson.interaction.maximum_attempts == 3
    assert lesson.explanation.text == "Existing validated explanation."
    arrows = [
        drawing for drawing in lesson.explanation.visualizations if drawing.command == "draw_arrow"
    ]
    assert len(arrows) == 1
    assert arrows[0].origin + arrows[0].target == candidate.move[:4]
    animations = [
        drawing
        for drawing in lesson.explanation.visualizations
        if drawing.command == "animate_line"
    ]
    assert animations[0].moves == candidate.line
    for hint in lesson.hints[:2]:
        assert candidate.san not in hint.model_dump_json()
        assert candidate.move not in hint.model_dump_json()
    previous = lesson.model_dump_json()
    fill_lesson_hints(lesson, analysis)
    assert lesson.model_dump_json() == previous
