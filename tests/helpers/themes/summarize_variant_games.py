"""Check the variant theme thresholds on the recorded games; no engine (#506).

Usage: uv run python -m tests.helpers.themes.summarize_variant_games

The rule is in ``record_variant_games``. A mistake is a move that loses
``MISTAKE_CP`` or more whose window has the half-pawn fall of section 18.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import chess

from scan64.explanations import themes
from scan64.explanations.themes import VARIANT_THEME_ORDER, GameTrace, ThemeKey, trace_from_scores
from tests.helpers.themes.record_variant_games import MAX_ORDINARY_SHARE, ORDINARY_CP, OUTPUT

MISTAKE_CP = 150
# Thresholds the themes had before the check; the rule only raises them.
STATED: dict[ThemeKey, int] = {
    "pocket_deficit": 3,
    "king_drop_squares": 2,
    "drop_threats": 2,
    "checks_behind": 1,
    "check_exposure": 2,
    "explosion_threats": 1,
}
MAX_THRESHOLD = 40


@dataclass(frozen=True)
class Window:
    boards: tuple[chess.Board, ...]
    learner: chess.Color


def windows(trace: GameTrace) -> tuple[list[Window], list[Window]]:
    """The game's ordinary windows and its mistakes' windows."""
    ordinary, mistakes = [], []
    for index in range(len(trace.moves)):
        plies = themes.window_plies(index)
        if len(plies) < themes.THEME_MIN_POSITIONS:
            continue
        learner = trace.boards[index].turn
        scores = [themes._learner_score(trace, ply, learner) for ply in (*plies, index + 1)]
        known = [score for score in scores if score is not None]
        if len(known) != len(scores):
            continue
        fell = known[-1] - known[0] <= -themes.THEME_TREND_CP
        window = Window(tuple(trace.boards[ply] for ply in plies), learner)
        if fell and known[-2] - known[-1] >= MISTAKE_CP:
            mistakes.append(window)
        elif not fell and all(abs(score) <= ORDINARY_CP for score in known):
            ordinary.append(window)
    return ordinary, mistakes


def _held(key: ThemeKey, window: Window, threshold: int) -> bool:
    with patch.dict(themes._VARIANT_THRESHOLDS, {key: threshold}):
        held = [themes._measure(key, board, window.learner).holds for board in window.boards]
    return sum(held) >= themes.THEME_MIN_POSITIONS and held[-1]


def _count(key: ThemeKey, found: list[Window], threshold: int) -> int:
    return sum(_held(key, window, threshold) for window in found)


def summarize(path: Path = OUTPUT) -> dict[str, Any]:
    recorded = json.loads(path.read_text(encoding="utf-8"))["games"]
    summary: dict[str, Any] = {}
    for variant, games in recorded.items():
        half = len(games) // 2
        parts = []
        for part in (games[:half], games[half:]):
            ordinary: list[Window] = []
            mistakes: list[Window] = []
            for game in part:
                trace = trace_from_scores(None, game["moves"], game["white_cp"], variant)
                found = windows(trace)
                ordinary += found[0]
                mistakes += found[1]
            parts.append((ordinary, mistakes))
        (first, first_mistakes), (second, second_mistakes) = parts
        for key in VARIANT_THEME_ORDER[variant]:
            threshold = next(
                (
                    value
                    for value in range(STATED[key], MAX_THRESHOLD)
                    if _count(key, first, value) <= MAX_ORDINARY_SHARE * len(first)
                ),
                None,
            )
            rows = {}
            for value in dict.fromkeys((STATED[key], threshold)):
                if value is None:
                    continue
                rows[str(value)] = {
                    "first_ordinary": [_count(key, first, value), len(first)],
                    "first_mistakes": [_count(key, first_mistakes, value), len(first_mistakes)],
                    "second_ordinary": [_count(key, second, value), len(second)],
                    "second_mistakes": [_count(key, second_mistakes, value), len(second_mistakes)],
                }
            summary[key] = {"stated": STATED[key], "threshold": threshold, "held": rows}
    return summary


if __name__ == "__main__":
    print(json.dumps(summarize(), indent=1))
