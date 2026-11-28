"""Record real variant games with variant engine scores for the threshold check (#506).

Usage:
    uv run python -m tests.helpers.themes.record_variant_games \\
        --engine FAIRY_STOCKFISH --dumps DIRECTORY

``DIRECTORY`` holds the Lichess monthly dumps named in ``DUMPS`` (CC0,
https://database.lichess.org). Per variant the first ``GAMES`` games of the dump,
in file order, that meet ``_qualifies``; the first half sets a threshold, the
second half checks it. Each position is searched once by the fast pass
(``NODES``, 1 thread, 16 MB hash) under the variant's rules.

The rule, fixed before any of these games was searched: an ordinary window is a
window of section 18 whose scores, and the score after its last move, are all
within ``ORDINARY_CP`` of equal, without the half-pawn fall. A theme's signal
passes its threshold in at most ``MAX_ORDINARY_SHARE`` of the ordinary windows
of the first half, or its threshold rises to the lowest value that does.
``summarize_variant_games`` recomputes it without an engine.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess.pgn

from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.chess.variants import VariantName, board_variant, pgn_san
from scan64.explanations.themes import game_trace
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePool, PoolBoundAdapter
from tests.helpers.themes.provenance import REPO_ROOT, code_provenance, engine_manifest

DUMPS: dict[VariantName, str] = {
    "crazyhouse": "lichess_db_crazyhouse_rated_2016-01.pgn.zst",
    "3check": "lichess_db_threeCheck_rated_2014-08.pgn.zst",
    "atomic": "lichess_db_atomic_rated_2015-02.pgn.zst",
}
GAMES = 200
MIN_RATING = 1600
MIN_PLIES = 20
NODES = 10_000
THREADS = 1
HASH_MB = 16
ORDINARY_CP = 150
MAX_ORDINARY_SHARE = 0.10
OUTPUT = REPO_ROOT / "tests" / "fixtures" / "themes" / "variant_game_scores.json"


def config() -> dict[str, Any]:
    return {
        "nodes": NODES,
        "threads": THREADS,
        "hash_mb": HASH_MB,
        "games_per_variant": GAMES,
        "min_rating": MIN_RATING,
        "min_plies": MIN_PLIES,
    }


def _qualifies(game: chess.pgn.Game) -> bool:
    headers = game.headers
    ratings = (headers.get("WhiteElo", "?"), headers.get("BlackElo", "?"))
    return (
        not game.errors
        and "FEN" not in headers
        and headers.get("Termination") == "Normal"
        and all(rating.isdigit() and int(rating) >= MIN_RATING for rating in ratings)
        and len(list(game.mainline_moves())) >= MIN_PLIES
    )


def _games(dump: Path) -> Iterator[chess.pgn.Game]:
    text = subprocess.run(["zstd", "-dc", str(dump)], check=True, capture_output=True).stdout
    stream = io.StringIO(text.decode())
    while (game := chess.pgn.read_game(stream)) is not None:
        yield game


def select(dump: Path, variant: VariantName) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for game in _games(dump):
        if len(found) == GAMES:
            break
        if not _qualifies(game):
            continue
        board = game.board()
        assert board_variant(board) == variant
        san = []
        for move in game.mainline_moves():
            san.append(pgn_san(board, move))
            board.push(move)
        moves = [move.uci() for move in game.mainline_moves()]
        found.append({"id": game.headers["Site"], "san": san, "moves": moves})
    assert len(found) == GAMES
    return found


async def record(engine: str, dumps: Path, concurrency: int, output: Path) -> None:
    files = {variant: dumps / name for variant, name in DUMPS.items()}
    provenance = code_provenance(Path(__file__), dict(files), config())
    pool = EnginePool(
        StockfishConfig(binary_path=engine, threads=THREADS, hash_size=HASH_MB), concurrency
    )
    adapter = PoolBoundAdapter(pool)
    orchestrator = FastPassOrchestrator(adapter, FastPassConfig(nodes=NODES))

    async def scored(game: dict[str, Any], variant: VariantName) -> dict[str, Any]:
        result = await orchestrator.run_fast_pass(game["san"], variant=variant)
        trace = game_trace(None, game["moves"], result.evaluations, variant)
        return {"id": game["id"], "moves": game["moves"], "white_cp": trace.scores}

    try:
        identity = await adapter.analysis_identity()
        games = {}
        for variant, path in files.items():
            selected = select(path, variant)
            games[variant] = await asyncio.gather(*(scored(g, variant) for g in selected))
    finally:
        await pool.close()
    record = {
        "source": "Lichess rated variant games (CC0), https://database.lichess.org",
        "dumps": DUMPS,
        "provenance": provenance,
        "engine": engine_manifest(identity),
        "config": config(),
        "games": games,
    }
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True)
    parser.add_argument("--dumps", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"{args.output} exists; a recording is never overwritten")
    asyncio.run(record(args.engine, args.dumps, args.concurrency, args.output))


if __name__ == "__main__":
    main()
