"""Position health: factor groups mapped to meters (#416)."""

from __future__ import annotations

import time

import chess
import pytest

from scan64.explanations.factor_facts import HEALTH_GROUPS, HEALTH_SCALE, health_values
from scan64.explanations.factors import evaluate_factors
from scan64.explanations.factors.report import PAWN_VALUE_EG
from scan64.learning.position_health import LEVELS, level, outcome, position_health, trend

# White's king is stripped of its pawns; Black's queen and rook bear on it.
EXPOSED = "r1b1k2r/ppp2ppp/2n5/3q4/8/8/PPP3PP/RNB1K1NR w KQkq - 0 1"


# Rook and two pawns each: material was exchanged, not lost (#424 review).
BALANCED_ENDGAME = "8/5pk1/4p3/8/8/1P6/1P3K2/R6r w - - 0 38"


def test_start_reads_equal_for_both_sides() -> None:
    readings = position_health(chess.Board(), "white").groups
    assert tuple(g.key for g in readings) == HEALTH_GROUPS
    for group in readings:
        assert group.learner == group.opponent
        assert group.learner.trend == "steady"


def test_meters_read_the_fitted_scale_of_the_net_value() -> None:
    board = chess.Board(BALANCED_ENDGAME)
    report = evaluate_factors(board)
    reading = position_health(board, "white")
    for side, values in (
        ("learner", health_values(report, chess.WHITE)),
        ("opponent", health_values(report, chess.BLACK)),
    ):
        for group in (reading.overall, *reading.groups):
            expected = round(HEALTH_SCALE[group.key].health(values[group.key]), 3)
            assert getattr(group, side).health == expected


def test_mutual_exchanges_keep_material_healthy_for_both_sides() -> None:
    material = {g.key: g for g in position_health(chess.Board(BALANCED_ENDGAME), "white").groups}
    assert material["Material"].learner.level == "good"
    assert material["Material"].opponent.level == "good"


def test_terms_are_the_largest_net_parts_of_the_value() -> None:
    board = chess.Board(EXPOSED)
    report = evaluate_factors(board)
    king = {g.key: g for g in position_health(board, "white").groups}["King"]
    parts = sorted(
        (part.tapered / PAWN_VALUE_EG for part in report.contributions if part.group == "King"),
        key=abs,
        reverse=True,
    )
    # The parts add up to the meter's value; the details show the largest three.
    assert abs(sum(parts) - health_values(report, chess.WHITE)["King"]) < 0.02
    assert [t.pawns for t in king.learner.terms] == [round(p, 2) for p in parts[:3]]


def test_exposed_king_reads_low_for_its_side_only() -> None:
    king = {g.key: g for g in position_health(chess.Board(EXPOSED), "white").groups}["King"]
    assert king.learner.level == "low"
    assert king.opponent.level == "good"
    assert king.learner.terms[0].pawns < 0


def test_learner_colour_swaps_the_sides() -> None:
    as_white = position_health(chess.Board(EXPOSED), "white")
    as_black = position_health(chess.Board(EXPOSED), "black")
    assert as_white.overall.learner == as_black.overall.opponent
    for white, black in zip(as_white.groups, as_black.groups, strict=True):
        assert white.learner == black.opponent


def test_previous_position_gives_the_trend() -> None:
    before = chess.Board()
    after = chess.Board()
    after.push_san("e4")
    readings = {g.key: g for g in position_health(after, "black", before).groups}
    assert readings["Mobility"].opponent.trend == "rising"


@pytest.mark.parametrize(
    ("value", "expected"), [(LEVELS["good"], "good"), (LEVELS["fair"], "fair"), (0.0, "low")]
)
def test_level_boundaries(value: float, expected: str) -> None:
    assert level(value) == expected


def test_trend_step() -> None:
    assert trend(0.5, 0.52) == "steady"
    assert trend(0.5, 0.6) == "falling"


def test_a_position_reads_within_the_panel_budget() -> None:
    board = chess.Board(EXPOSED)
    position_health(board, "white")
    started = time.perf_counter()
    position_health(board, "white", chess.Board())
    assert time.perf_counter() - started < 0.1


def test_overall_reads_the_factor_total_without_tempo() -> None:
    start = position_health(chess.Board(), "white").overall
    assert start.learner == start.opponent
    assert start.learner.level == "good"
    # With the fifty-move clock running, tempo is scaled with the rest (#425).
    late = position_health(chess.Board("8/8/8/8/8/8/1K6/7k w - - 90 50"), "white").overall
    assert position_health(chess.Board("8/8/8/8/8/8/1K6/7k b - - 90 50"), "white").overall == late
    # The same position with the other side to move: tempo must not move the meter.
    black_to_move = chess.Board(chess.STARTING_FEN.replace(" w ", " b "))
    assert position_health(black_to_move, "white").overall == start
    queenless = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNB1KBNR w KQkq - 0 1")
    overall = position_health(queenless, "white").overall
    assert overall.learner.level == "low"
    assert overall.opponent.level == "good"
    assert overall.learner.terms[0].name == "Material"
    assert overall.learner.terms[0].pawns < 0


def test_a_finished_game_reports_its_outcome() -> None:
    # The factor evaluation scores the pieces left, so a mated side's meters can read good;
    # the outcome tells the panel to show the result instead.
    mated = chess.Board()
    for san in ("f3", "e5", "g4", "Qh4#"):
        mated.push_san(san)
    assert position_health(mated, "white").outcome == "checkmate"
    assert outcome(chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")) == "stalemate"
    assert position_health(chess.Board(), "white").outcome is None
