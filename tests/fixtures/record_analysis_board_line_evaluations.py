"""Record the per-move searches along the recorded Analysis Board lines (#367).

Run from the repository root with:
    uv run python tests/fixtures/record_analysis_board_line_evaluations.py

For each position of ``analysis_board_lines.json``, lists the positions the
endpoint scores (``board_line_positions``: plies 2 to 8) for MultiPV 1 to 4 and
searches each one as production does: a pooled engine, ``LINE_NODES`` nodes,
MultiPV 1, the board with its moves from the position.
Stores the shared ``EngineAnalysis.config`` once and ``raw_result`` per
position, in order.
Also prints the engine time per position set. Tests never invoke the engine.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import chess

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.board_lines import board_line_positions, scored_legal_lines
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import LINE_NODES, PooledStockfishAdapter

ROOT = Path(__file__).resolve().parent
LINES = ROOT / "analysis_board_lines.json"
FIXTURE = ROOT / "analysis_board_line_evaluations.json"
REGENERATE = "uv run python tests/fixtures/record_analysis_board_line_evaluations.py"


async def main() -> None:
    recording = json.loads(LINES.read_text(encoding="utf-8"))
    adapter = PooledStockfishAdapter(StockfishConfig())
    await adapter.ensure_started()
    positions = {}
    configs = []
    try:
        for name, entry in recording["positions"].items():
            board = chess.Board(entry["fen"])
            analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
            lines = scored_legal_lines(board, analysis)
            # MultiPV 1 to 4 can list different positions (a later ply of one
            # line can repeat another line's first position).
            boards = list(
                {
                    tuple(position.move_stack): position
                    for count in range(1, len(lines) + 1)
                    for position in board_line_positions(board, lines[:count])
                }.values()
            )
            searched = []
            started = time.perf_counter()
            for position in boards:
                await adapter.reset()
                result = await adapter.analyze_position(position.copy(), nodes=LINE_NODES)
                configs.append(result.config)
                searched.append(
                    {
                        "moves": [move.uci() for move in position.move_stack],
                        "fen": position.fen(),
                        "raw_result": result.raw_result,
                    }
                )
            elapsed = time.perf_counter() - started
            print(f"{name}: {len(boards)} positions, {elapsed * 1000:.0f} ms")
            positions[name] = searched
    finally:
        await adapter.quit()
    if any(config != configs[0] for config in configs):
        raise RuntimeError("Searches ran with different engine identities")
    # The executable digest differs between platform builds; the UCI ``id name``
    # (name and version) must match the engine that found the lines.
    if configs[0]["engine_name"] != recording["engine"]["engine_name"]:
        raise RuntimeError(
            f"Lines were found by {recording['engine']['engine_name']}, "
            f"not {configs[0]['engine_name']}"
        )
    record = {
        "config": configs[0],
        "nodes": LINE_NODES,
        "multipv": 1,
        "recorded_on": datetime.now(UTC).date().isoformat(),
        "regenerate_command": REGENERATE,
        "positions": positions,
    }
    FIXTURE.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {FIXTURE}")


if __name__ == "__main__":
    asyncio.run(main())
