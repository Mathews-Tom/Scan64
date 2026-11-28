"""Record the raw scores of the turning point measurement (#486, #513).

Usage:
    uv run python -m tests.helpers.themes.record_turning_points --engine STOCKFISH

Every position of every game is searched once per budget (``NODES``) with the
pooled batch adapter that game analysis uses (1 thread, 16 MB hash,
``ucinewgame`` before each search). The output holds the White-view score of
each position, as ``game_trace`` reads it; ``summarize_turning_points``
recomputes every figure of docs/narrator-pipeline.md section 18 from it. The
output file is created once and never overwritten: a partial run writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import chess

from scan64.explanations.themes import game_trace
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePool, PoolBoundAdapter
from tests.helpers.themes.provenance import REPO_ROOT, code_provenance, engine_manifest

NODES = (10_000, 100_000, 1_000_000)
THREADS = 1
HASH_MB = 16
_M48 = REPO_ROOT / "tests" / "fixtures" / "m48"
GAMES = {
    "slow": REPO_ROOT / "benchmarks" / "factor_calibration" / "games.json",
    # The 80 games the rule was chosen on (#486).
    "selection": _M48 / "m79_fresh80_recorded.json",
    # Games that did not select the rule (#513).
    "unselected_m93": _M48 / "m93_fresh80_recorded.json",
    "unselected_m94": _M48 / "m94_v2_fresh80_recorded.json",
}
OUTPUT = REPO_ROOT / "tests" / "fixtures" / "themes" / "turning_point_scores.json"


def load_games(name: str) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = json.loads(GAMES[name].read_text(encoding="utf-8"))["games"]
    return games


def config() -> dict[str, Any]:
    return {"nodes": NODES, "threads": THREADS, "hash_mb": HASH_MB, "multipv": 1}


async def _score_game(adapter: PoolBoundAdapter, game: dict[str, Any]) -> dict[str, Any]:
    board = chess.Board()
    fens, moves = [board.fen()], []
    for san in game["moves"]:
        moves.append(board.push_san(san).uci())
        fens.append(board.fen())
    scores = {}
    for nodes in NODES:
        analyses = await asyncio.gather(
            *(adapter.analyze_position(fen, nodes=nodes) for fen in fens)
        )
        scores[str(nodes)] = game_trace(None, moves, analyses).scores
    return {"id": game["id"], "scores": scores}


async def record(engine: str, concurrency: int, output: Path) -> None:
    provenance = code_provenance(Path(__file__), GAMES, config())
    pool = EnginePool(
        StockfishConfig(binary_path=engine, threads=THREADS, hash_size=HASH_MB), concurrency
    )
    adapter = PoolBoundAdapter(pool)
    try:
        identity = await adapter.analysis_identity()
        sets = {
            name: await asyncio.gather(*(_score_game(adapter, game) for game in load_games(name)))
            for name in GAMES
        }
    finally:
        await pool.close()
    record = {
        "provenance": provenance,
        "engine": engine_manifest(identity),
        "config": config(),
        "sets": sets,
    }
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"{args.output} exists; a recording is never overwritten")
    asyncio.run(record(args.engine, args.concurrency, args.output))


if __name__ == "__main__":
    main()
