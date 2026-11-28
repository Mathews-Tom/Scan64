"""Recompute the turning point figures from the recorded scores; no engine (#513).

Usage: uv run python -m tests.helpers.themes.summarize_turning_points

A result is the turning point's ply of one side of one game, or ``None``. The
reference is the same rule on the scores at 1 000 000 nodes. "Sides" are the
sides with a turning point on the fast pass or at 1 000 000 nodes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from statistics import mean, median
from typing import Any
from unittest.mock import patch

import chess

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations import themes
from scan64.explanations.themes import GameTrace, trace_from_scores, turning_point
from tests.helpers.themes.record_turning_points import OUTPUT, load_games

FAST, MIDDLE, DEEP = "10000", "100000", "1000000"
CHECKS = ((25, MIDDLE), (25, DEEP), (50, MIDDLE), (50, DEEP))
RULE = "check 50 cp, 1000000 nodes"
type Result = int | None


def _result(trace: GameTrace, learner: chess.Color) -> Result:
    point = turning_point(trace, 0, learner)
    return point.ply if point is not None else None


def check(
    fast: GameTrace, deep: GameTrace, learner: chess.Color, within_cp: int
) -> tuple[Result, int]:
    """``check_level_scores`` on the recorded scores: the result and its searches."""
    searched = 0

    async def search(index: int) -> EngineAnalysis:
        nonlocal searched
        searched += 1
        score = deep.scores[index]
        assert score is not None
        sign = 1 if deep.boards[index].turn == chess.WHITE else -1
        return EngineAnalysis(config={}, raw_result=[{"score_cp": sign * score}])

    with patch.object(themes, "LEVEL_CHECK_CP", within_cp):
        trace = asyncio.run(themes.check_level_scores(fast, learner, search))
    return _result(trace, learner), searched


def smooth(trace: GameTrace, width: int, how: Callable[[list[int]], float]) -> GameTrace:
    """Centred smoothing of the clamped White-view scores; the window is cut at the ends."""
    limit = themes.SCORE_CLAMP_CP
    scores = [max(-limit, min(limit, s)) for s in trace.scores if s is not None]
    assert len(scores) == len(trace.scores)
    half = width // 2
    smoothed = [round(how(scores[max(0, i - half) : i + half + 1])) for i in range(len(scores))]
    return GameTrace(trace.boards, trace.moves, tuple(smoothed))


def _side(traces: dict[str, GameTrace], learner: chess.Color) -> tuple[dict[str, Result], int]:
    results = {
        "fast pass": _result(traces[FAST], learner),
        "every position, 100000 nodes": _result(traces[MIDDLE], learner),
        "every position, 1000000 nodes": _result(traces[DEEP], learner),
        "median of 3": _result(smooth(traces[FAST], 3, median), learner),
        "median of 5": _result(smooth(traces[FAST], 5, median), learner),
        "mean of 3": _result(smooth(traces[FAST], 3, mean), learner),
    }
    searches = 0
    for within, nodes in CHECKS:
        name = f"check {within} cp, {nodes} nodes"
        results[name], count = check(traces[FAST], traces[nodes], learner, within)
        if name == RULE:
            searches = count
    return results, searches


def _lost(trace: GameTrace, learner: chess.Color) -> bool:
    last = trace.scores[-1]
    assert last is not None
    return (last if learner == chess.WHITE else -last) <= -themes.LOST_CP


def summarize_set(recorded: list[dict[str, Any]], games: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[tuple[dict[str, Result], dict[str, Any], chess.Color]] = []
    searches = lost_sides = 0
    differ: dict[str, dict[str, Result]] = {}
    for record, game in zip(recorded, games, strict=True):
        assert record["id"] == game["id"]
        traces = {
            nodes: trace_from_scores(None, game["moves"], scores)
            for nodes, scores in record["scores"].items()
        }
        sides: tuple[chess.Color, ...] = (chess.WHITE, chess.BLACK)
        if "winner" in game:  # a slow-loss game: the loser only
            sides = (chess.BLACK if game["winner"] == "white" else chess.WHITE,)
        for learner in sides:
            results, count = _side(traces, learner)
            searches += count
            lost_sides += _lost(traces[FAST], learner)
            if "lichess_white_cp" in game:
                lichess = trace_from_scores(None, game["moves"], [None, *game["lichess_white_cp"]])
                results["Lichess"] = _result(lichess, learner)
            rows.append((results, game, learner))
            if results["fast pass"] != results["every position, 1000000 nodes"]:
                differ[f"{game['id']} {'white' if learner else 'black'}"] = {
                    name: results[name]
                    for name in ("every position, 1000000 nodes", "fast pass", RULE)
                }
    reference = "every position, 1000000 nodes"
    with_point = [r for r, _, _ in rows if r["fast pass"] is not None or r[reference] is not None]
    summary: dict[str, Any] = {
        "games": len(games),
        "sides": len(rows),
        "sides_with_a_point": len(with_point),
        "same_as_deep": {
            name: sum(r[name] == r[reference] for r in with_point) for name in rows[0][0]
        },
        "lost_sides": lost_sides,
        "searches_per_lost_side": round(searches / lost_sides, 1),
        "fast_pass_differs": differ,
    }
    if "Lichess" in rows[0][0]:
        summary["same_as_lichess_all_games"] = {
            name: sum(r[name] == r["Lichess"] for r, _, _ in rows) for name in rows[0][0]
        }
    return summary


def summarize(path: Path = OUTPUT) -> dict[str, Any]:
    sets = json.loads(path.read_text(encoding="utf-8"))["sets"]
    summary = {name: summarize_set(recorded, load_games(name)) for name, recorded in sets.items()}
    unselected = [name for name in sets if name.startswith("unselected")]
    summary["unselected"] = summarize_set(
        [game for name in unselected for game in sets[name]],
        [game for name in unselected for game in load_games(name)],
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(summarize(), indent=1))
