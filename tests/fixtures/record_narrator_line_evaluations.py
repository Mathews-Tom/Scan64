"""Record the per-move searches along the 50 narrator benchmark lessons' lines (#367).

Run from the repository root with:
    uv run python tests/fixtures/record_narrator_line_evaluations.py

For each lesson of ``benchmarks/narrator/positions.json`` (evidence from the
recorded Stockfish searches of ``tests/fixtures/m48``), runs the production
``score_verified_lines`` with one warm pooled engine (``LINE_NODES`` nodes,
MultiPV 1, concurrency 1) and stores each searched FEN's score, depth and nodes. Prints
the searches and engine time per lesson. Tests never invoke the engine.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scan64.chess.analysis.line_scoring import score_verified_lines
from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.facts import LINE_NODES
from scan64.learning.evidence.models import Evidence
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import PooledStockfishAdapter

ROOT = Path(__file__).resolve().parents[2]
POSITIONS = ROOT / "benchmarks/narrator/positions.json"
FIXTURE = ROOT / "tests/fixtures/narrator_line_evaluations.json"
REGENERATE = "uv run python tests/fixtures/record_narrator_line_evaluations.py"
_KEPT = ("score_cp", "score_mate", "depth", "nodes")


class _Recorder:
    def __init__(self, adapter: PooledStockfishAdapter) -> None:
        self.adapter = adapter
        self.searched: dict[str, list[dict[str, Any]]] = {}
        self.configs: list[dict[str, Any]] = []

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        await self.adapter.reset()
        analysis = await self.adapter.analyze_position(fen, nodes=nodes, multipv=multipv)
        # Only the scores are read back; the PV and search statistics are dropped.
        self.searched[fen] = [
            {key: entry.get(key) for key in _KEPT} for entry in analysis.raw_result[:1]
        ]
        self.configs.append(analysis.config)
        return analysis


async def main() -> None:
    lessons = json.loads(POSITIONS.read_text(encoding="utf-8"))["positions"]
    adapter = PooledStockfishAdapter(StockfishConfig())
    await adapter.ensure_started()
    recorder = _Recorder(adapter)
    counts, times = [], []
    try:
        for lesson in lessons:
            evidence = [Evidence.model_validate(item) for item in lesson["evidence"]]
            started = time.perf_counter()
            count = await score_verified_lines(
                recorder, evidence, lesson["diagnosis"]["evidence_refs"], lesson["fen"]
            )
            times.append((time.perf_counter() - started) * 1000)
            counts.append(count)
    finally:
        await adapter.quit()
    if any(config != recorder.configs[0] for config in recorder.configs):
        raise RuntimeError("Searches ran with different engine identities")
    print(
        f"{len(lessons)} lessons: searches per lesson median {statistics.median(counts)}, "
        f"max {max(counts)}; engine ms per lesson median {statistics.median(times):.0f}, "
        f"max {max(times):.0f}, total {sum(times):.0f}"
    )
    record = {
        "config": recorder.configs[0],
        "nodes": LINE_NODES,
        "multipv": 1,
        "recorded_on": datetime.now(UTC).date().isoformat(),
        "regenerate_command": REGENERATE,
        "searches": recorder.searched,
    }
    FIXTURE.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {FIXTURE}")


if __name__ == "__main__":
    asyncio.run(main())
