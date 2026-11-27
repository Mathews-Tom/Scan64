"""Position health: factor groups mapped to meters (#416)."""

from __future__ import annotations

import time

import chess
import pytest

from scan64.learning.position_health import (
    HEALTH_SCALE,
    LEVELS,
    health,
    level,
    position_health,
    trend,
)

# White's king is stripped of its pawns; Black's queen and rook bear on it.
EXPOSED = "r1b1k2r/ppp2ppp/2n5/3q4/8/8/PPP3PP/RNB1K1NR w KQkq - 0 1"


def test_start_reads_healthy_and_equal_for_both_sides() -> None:
    readings = position_health(chess.Board(), "white")
    assert [g.key for g in readings] == [g.key for g in HEALTH_SCALE]
    for group in readings:
        assert group.learner == group.opponent
        assert group.learner.level == "good"
        assert group.learner.trend == "steady"


def test_exposed_king_reads_low_for_its_side_only() -> None:
    king = {g.key: g for g in position_health(chess.Board(EXPOSED), "white")}["King"]
    assert king.learner.level == "low"
    assert king.opponent.level == "good"
    assert king.learner.terms[0].name == "King danger"
    assert king.learner.terms[0].pawns < 0


def test_learner_colour_swaps_the_sides() -> None:
    as_white = position_health(chess.Board(EXPOSED), "white")
    as_black = position_health(chess.Board(EXPOSED), "black")
    for white, black in zip(as_white, as_black, strict=True):
        assert white.learner == black.opponent


def test_previous_position_gives_the_trend() -> None:
    before = chess.Board()
    after = chess.Board()
    after.push_san("e4")
    readings = {g.key: g for g in position_health(after, "black", before)}
    assert readings["Mobility"].opponent.trend == "rising"


@pytest.mark.parametrize(
    ("value", "expected"), [(LEVELS["good"], "good"), (LEVELS["fair"], "fair"), (0.0, "low")]
)
def test_level_boundaries(value: float, expected: str) -> None:
    assert level(value) == expected


def test_health_is_monotonic_and_bounded() -> None:
    values = [health(v, 0.0, 0.5) for v in (-5, -1, 0, 1, 5)]
    assert values == sorted(values)
    assert all(0 < v < 1 for v in values)
    assert trend(0.5, 0.52) == "steady"
    assert trend(0.5, 0.6) == "falling"


def test_a_position_reads_within_the_panel_budget() -> None:
    board = chess.Board(EXPOSED)
    position_health(board, "white")
    started = time.perf_counter()
    position_health(board, "white", chess.Board())
    assert time.perf_counter() - started < 0.1
