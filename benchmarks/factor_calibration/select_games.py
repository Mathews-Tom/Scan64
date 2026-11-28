"""Select public Lichess games that one side lost slowly (issue #410).

Usage:
    curl -H "Accept: application/x-ndjson" \\
        "https://lichess.org/api/tournament/<id>/games?evals=true&moves=true" > <id>.ndjson
    uv run python -m benchmarks.factor_calibration.select_games a.ndjson b.ndjson \\
        --tournament a b --out benchmarks/factor_calibration/games.json

The input is the Lichess tournament export (NDJSON, no account needed) with the
Lichess server analysis (``analysis``: one eval per ply, White's view). Only
games with that analysis are read. The output keeps the game URL, the ratings,
the result, the moves and the Lichess evals; it keeps no user names.

"Lost slowly" (all must hold, loser's view, mate = +-1000 cp, evals clamped to
+-1000):

1. decisive by resignation, mate or flag, at least ``MIN_PLIES`` plies;
2. the loser's eval reaches ``LOST_CP`` or less at a ply ``p_lost``;
3. the last ply before ``p_lost`` with eval >= ``LEVEL_CP`` is at least
   ``MIN_DECLINE_PLIES`` plies earlier (a slow decline, not one move);
4. no loser move in that decline loses more than ``MAX_DROP_CP``.

Candidate mistakes are the loser's moves in the decline that lose at least
``MIN_DROP_CP`` by the Lichess evals, largest first, at most
``CANDIDATES_PER_GAME`` per game. Null moves (``NULL_PER_GAME`` per game) are
moves of either side that lose at most ``NULL_DROP_CP`` while the eval is within
``+-NULL_LEVEL_CP``, chosen by a seeded shuffle. ``record_positions`` confirms
both with Stockfish.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

MIN_PLIES = 50
LOST_CP = -300
LEVEL_CP = -60
MIN_DECLINE_PLIES = 16
MAX_DROP_CP = 200
MIN_DROP_CP = 40
CANDIDATES_PER_GAME = 3
NULL_DROP_CP = 10
NULL_LEVEL_CP = 200
NULL_PER_GAME = 12
SEED = 410


def _white_cp(entry: dict[str, Any]) -> int:
    if "mate" in entry:
        return 1000 if entry["mate"] > 0 else -1000
    return max(-1000, min(1000, int(entry["eval"])))


def select(game: dict[str, Any]) -> dict[str, Any] | None:
    moves = game["moves"].split()
    analysis = game.get("analysis")
    winner = game.get("winner")
    if (
        analysis is None
        or winner is None
        or game.get("variant") != "standard"
        or game["status"] not in ("resign", "mate", "outoftime")
        or len(moves) < MIN_PLIES
        or len(analysis) < len(moves) - 1
    ):
        return None
    loser_white = winner == "black"
    sign = 1 if loser_white else -1
    # evals[k]: loser's view after ply k (k = 0: start position, taken as 0).
    evals = [0] + [sign * _white_cp(entry) for entry in analysis[: len(moves)]]
    lost = next((k for k, value in enumerate(evals) if value <= LOST_CP), None)
    if lost is None:
        return None
    level = max((k for k in range(lost) if evals[k] >= LEVEL_CP), default=None)
    if level is None or lost - level < MIN_DECLINE_PLIES:
        return None
    # Ply k (1-based) is move index k - 1; the loser moves at even indices when White.
    loser_indices = [i for i in range(level, lost) if (i % 2 == 0) == loser_white]
    drops = {i: evals[i] - evals[i + 1] for i in loser_indices}
    if any(drop > MAX_DROP_CP for drop in drops.values()):
        return None
    candidates = sorted(
        (i for i, drop in drops.items() if drop >= MIN_DROP_CP), key=lambda i: (-drops[i], i)
    )[:CANDIDATES_PER_GAME]
    if not candidates:
        return None
    nulls = []
    for i in range(10, len(moves) - 1):
        mover = 1 if (i % 2 == 0) == loser_white else -1
        before, after = mover * evals[i], mover * evals[i + 1]
        if abs(before) <= NULL_LEVEL_CP and before - after <= NULL_DROP_CP and i not in candidates:
            nulls.append(i)
    random.Random(f"{SEED}:{game['id']}").shuffle(nulls)
    return {
        "id": game["id"],
        "url": f"https://lichess.org/{game['id']}",
        "tournament": f"https://lichess.org/tournament/{game['tournament']}",
        "speed": game["speed"],
        "clock": game.get("clock"),
        "white_rating": game["players"]["white"].get("rating"),
        "black_rating": game["players"]["black"].get("rating"),
        "winner": winner,
        "status": game["status"],
        "moves": moves,
        "lichess_white_cp": [_white_cp(entry) for entry in analysis[: len(moves)]],
        "decline": {"level_ply": level, "lost_ply": lost},
        "candidates": sorted(candidates),
        "nulls": sorted(nulls[:NULL_PER_GAME]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ndjson", type=Path, nargs="+", help="one export per tournament")
    parser.add_argument("--tournament", required=True, nargs="+", help="ids, same order")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-games", type=int, default=30)
    args = parser.parse_args()
    if len(args.ndjson) != len(args.tournament):
        parser.error("give one tournament id per export")
    games = [
        {**json.loads(line), "tournament": tournament}
        for path, tournament in zip(args.ndjson, args.tournament, strict=True)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    games.sort(key=lambda game: game["id"])
    selected = [record for game in games if (record := select(game)) is not None]
    args.out.write_text(
        json.dumps(
            {
                "sources": [
                    f"https://lichess.org/api/tournament/{tournament}/games?evals=true&moves=true"
                    for tournament in args.tournament
                ],
                "license": "Lichess game data, CC0",
                "selection": {
                    name: value
                    for name, value in globals().items()
                    if name.isupper() and isinstance(value, int)
                },
                "games_read": len(games),
                "games_with_analysis": sum("analysis" in game for game in games),
                "games_selected": len(selected[: args.max_games]),
                "games_matching": len(selected),
                "games": selected[: args.max_games],
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(selected)} games match; wrote {min(len(selected), args.max_games)}")


if __name__ == "__main__":
    main()
