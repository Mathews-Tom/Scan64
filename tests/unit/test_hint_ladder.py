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
LIVE_FEN = "r1bq1rk1/pppp1ppp/2n2n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R w KQ - 0 6"


def highlighted_squares(visualizations):
    assert all(drawing.command == "highlight_square" for drawing in visualizations)
    return {drawing.square for drawing in visualizations}


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
            squares = highlighted_squares(step.visualizations)
            endpoints = {
                square
                for candidate in insight.candidates
                for square in (candidate.move[:2], candidate.move[2:4])
            }
            assert squares.isdisjoint(endpoints)
            assert squares.isdisjoint({insight_step(board, insight, 2).square})
            assert len(squares) == 1
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
        ("7k/8/8/8/4n3/8/4R3/4K3 w - - 0 1", "e2e4"),
        (chess.STARTING_FEN, "e2e4"),
        (chess.STARTING_FEN, "g1f3"),
        (RECORDING["positions"]["C"]["fen"], "e1c1"),
    ],
    ids=["free-capture", "pawn", "development", "castling"],
)
def test_capture_pawn_development_and_castling_do_not_leak_move_endpoints(fen, move):
    board = chess.Board(fen)
    insight = position_insight(board, EngineAnalysis(raw_result=[{"pv": [move], "score_cp": 0}]))
    nudge = insight_step(board, insight, 1)
    idea = insight_step(board, insight, 2)
    squares = highlighted_squares(nudge.visualizations)
    assert squares.isdisjoint({move[:2], move[2:4]})
    assert idea.square not in squares
    assert len(nudge.visualizations) == 1
    for level in (1, 2):
        serialized = insight_step(board, insight, level).model_dump_json()
        assert move not in serialized
        assert board.san(chess.Move.from_uci(move)) not in serialized
        assert move[2:4] not in serialized


def test_quiet_pawn_nudge_does_not_identify_the_only_candidate_piece():
    board = chess.Board(LIVE_FEN)
    insight = position_insight(
        board,
        EngineAnalysis(
            raw_result=[
                {"pv": ["h2h3"], "score_cp": 31},
                {"pv": ["h2h4"], "score_cp": 24},
            ]
        ),
    )
    nudge = insight_step(board, insight, 1)
    idea = insight_step(board, insight, 2)
    assert [candidate.move for candidate in insight.candidates] == ["h2h3", "h2h4"]
    assert highlighted_squares(nudge.visualizations).isdisjoint({"h2", "h3", "h4"})
    assert len(nudge.visualizations) == 1
    assert highlighted_squares(nudge.visualizations).isdisjoint({idea.square})
    assert nudge.text == "How is the highlighted pawn defended?"
    assert idea.square == "h2"


def test_nudge_does_not_highlight_another_candidates_origin():
    board = chess.Board("7k/8/8/1b6/8/8/4R3/R3K3 w - - 0 1")
    insight = position_insight(
        board,
        EngineAnalysis(
            raw_result=[
                {"pv": ["a1a2"], "score_cp": 0},
                {"pv": ["e2e3"], "score_cp": 0},
            ]
        ),
    )
    assert insight.candidates[0].reasons[0].squares == ["e2"]
    assert highlighted_squares(insight_step(board, insight, 1).visualizations).isdisjoint(
        {"a1", "a2", "e2", "e3"}
    )


def test_quiet_nudge_prioritizes_the_most_valuable_legally_attacked_opponent_piece():
    board = chess.Board("7k/8/8/r3q3/8/8/R3R3/7K w - - 0 1")
    insight = position_insight(board, EngineAnalysis(raw_result=[{"pv": ["h1g1"], "score_cp": 0}]))
    assert insight.candidates[0].fallback
    nudge = insight_step(board, insight, 1)
    assert highlighted_squares(nudge.visualizations) == {"e5"}
    assert nudge.text == "How is the highlighted queen defended?"


def test_quiet_nudge_uses_the_last_opponent_move_when_history_is_known():
    board = chess.Board()
    board.push_uci("e2e4")
    board.push_uci("e7e5")
    insight = position_insight(board, EngineAnalysis(raw_result=[{"pv": ["g1f3"], "score_cp": 0}]))
    nudge = insight_step(board, insight, 1)
    assert highlighted_squares(nudge.visualizations) == {"e5"}
    assert nudge.text == "What changed when your opponent moved this pawn?"


def test_quiet_nudge_uses_the_opponent_king_when_no_attacked_piece_or_history_exists():
    board = chess.Board()
    insight = position_insight(board, EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 0}]))
    nudge = insight_step(board, insight, 1)
    assert highlighted_squares(nudge.visualizations) == {"e8"}
    assert nudge.text == "What can you notice about the opposing king's safety?"


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
    accepted = {board.parse_san(move.san).uci() for move in lesson.interaction.accepted_moves}
    endpoints = {square for move in accepted for square in (move[:2], move[2:4])}
    assert highlighted_squares(lesson.hints[0].visualizations).isdisjoint(endpoints)
    previous = lesson.model_dump_json()
    fill_lesson_hints(lesson, analysis)
    assert lesson.model_dump_json() == previous


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fen", "moves", "cue"),
    [
        (
            "7k/8/8/1b6/8/8/4R3/R3K3 w - - 0 1",
            ["a1a2", "a1b1", "a1c1", "e2e3"],
            "e2",
        ),
        (
            RECORDING["positions"]["A"]["fen"],
            ["f1c4", "h5f3", "h5e2", "h5f7"],
            "f7",
        ),
    ],
    ids=["fourth-accepted-origin", "fourth-accepted-destination"],
)
async def test_lesson_early_targets_exclude_all_accepted_moves_beyond_candidate_cap(
    fen, moves, cue
):
    board = chess.Board(fen)
    analysis = EngineAnalysis(raw_result=[{"pv": [move], "score_cp": 0} for move in moves])
    insight = position_insight(board, analysis)
    assert len(insight.candidates) == 3
    assert cue in insight.candidates[0].reasons[0].squares
    assert all(
        cue not in (candidate.move[:2], candidate.move[2:4]) for candidate in insight.candidates
    )
    assert cue in (moves[3][:2], moves[3][2:4])
    lesson = await generate_exact_replay_exercise(
        Diagnosis(primary="test", confidence=1),
        fen,
        "all-accepted-hints",
        [board.san(chess.Move.from_uci(move)) for move in moves],
    )
    fill_lesson_hints(lesson, analysis)
    verify_lesson(lesson, analysis)
    endpoints = {square for move in moves for square in (move[:2], move[2:4])}
    assert highlighted_squares(lesson.hints[0].visualizations).isdisjoint(endpoints)
    assert len(lesson.hints[0].visualizations) == 1
    assert highlighted_squares(lesson.hints[0].visualizations).isdisjoint(
        {lesson.hints[1].visualizations[0].square}
    )
    idea_squares = highlighted_squares(lesson.hints[1].visualizations)
    assert cue not in idea_squares
    assert idea_squares.isdisjoint({move[2:4] for move in moves})


@pytest.mark.asyncio
async def test_quiet_lesson_nudge_excludes_accepted_captures_beyond_candidate_cap():
    board = chess.Board("7k/8/8/r3q3/8/8/R3R3/7K w - - 0 1")
    moves = ["h1g1", "e2e3", "a2a3", "e2e5", "a2a5"]
    analysis = EngineAnalysis(raw_result=[{"pv": [move], "score_cp": 0} for move in moves])
    insight = position_insight(board, analysis)
    assert len(insight.candidates) == 3
    assert insight.candidates[0].fallback
    lesson = await generate_exact_replay_exercise(
        Diagnosis(primary="test", confidence=1),
        board.fen(),
        "quiet-all-accepted-hints",
        [board.san(chess.Move.from_uci(move)) for move in moves],
    )
    fill_lesson_hints(lesson, analysis)
    verify_lesson(lesson, analysis)
    squares = highlighted_squares(lesson.hints[0].visualizations)
    assert squares == {"h8"}
    assert squares.isdisjoint({square for move in moves for square in (move[:2], move[2:4])})
    assert squares.isdisjoint({lesson.hints[1].visualizations[0].square})
