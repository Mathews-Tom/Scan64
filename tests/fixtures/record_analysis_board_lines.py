"""Regenerate the recorded Analysis Board engine lines used by #368 tests.

Run from the repository root with:
    uv run python tests/fixtures/record_analysis_board_lines.py

Searches each position with the production adapter at the built-in hint budget
(200 000 nodes, MultiPV 4, one thread; no time cap, so the recording does not
depend on machine speed) and stores ``EngineAnalysis.raw_result`` as written.
The record's ``engine`` key names the one engine that produced every line: its
UCI ``id name`` (for example ``Stockfish 18``), executable digest and network.
Tests never invoke the engine.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig
from scan64.providers.stockfish.pool import HINT_MULTIPV, HINT_NODES

FIXTURE = Path(__file__).resolve().parent / "analysis_board_lines.json"
POSITIONS = {
    "start": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "scholar_threat": "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
    "scholar_mate_in_one": "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
    "back_rank": "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1",
    "king_pawn": "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1",
    "italian_black": "r2q1rk1/ppp2ppp/2n1bn2/2bpp3/4P3/2PP1N2/PP1NBPPP/R1BQ1RK1 b - - 0 8",
    "promotion": "7k/P7/8/8/8/8/8/7K w - - 0 1",
    "open_centre": "rnbqkbnr/ppp2ppp/8/3pp3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq d6 0 3",
    "castling_choice": "r3k2r/pppq1ppp/2npbn2/4p3/2B1P3/2NP1N2/PPPQ1PPP/R3K2R w KQkq - 0 9",
    "queen_endgame_black": "8/2k5/8/8/3K4/8/8/7q b - - 0 1",
    "en_passant": "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",
    "rook_vs_knight_black": "6rk/6pp/8/6N1/8/8/8/6K1 b - - 0 1",
}


ENGINE_KEYS = ("engine_name", "engine_version", "network")


def engine_key(configs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The engine identity shared by every position's analysis config."""
    identities = {tuple(config[key] for key in ENGINE_KEYS) for config in configs}
    if len(identities) != 1:
        raise RuntimeError(f"Expected one engine identity, found {len(identities)}")
    (identity,) = identities
    return dict(zip(ENGINE_KEYS, identity, strict=True))


async def main() -> None:
    adapter = StockfishAdapter(StockfishConfig())
    record: dict[str, object] = {
        "nodes": HINT_NODES,
        "multipv": HINT_MULTIPV,
        "recorded_on": datetime.now(UTC).date().isoformat(),
        "regenerate_command": "uv run python tests/fixtures/record_analysis_board_lines.py",
    }
    positions: dict[str, dict[str, Any]] = {}
    for name, fen in POSITIONS.items():
        analysis = await adapter.analyze_position(fen, nodes=HINT_NODES, multipv=HINT_MULTIPV)
        positions[name] = {"fen": fen, "config": analysis.config, "raw_result": analysis.raw_result}
    record["engine"] = engine_key(entry["config"] for entry in positions.values())
    record["positions"] = positions
    FIXTURE.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {FIXTURE}")


if __name__ == "__main__":
    asyncio.run(main())
