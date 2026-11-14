"""Record M77 once, after approval of the committed pre-registration.

Run: uv run python scripts/record_lichess_theme_agreement.py
This explicit operation launches Stockfish; the offline scorer never does.
The output refuses overwrite. All engines are closed on success or failure.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.learning.evaluation.lichess_themes import (
    FIXTURES,
    Puzzle,
    PuzzleRecording,
    RecordedLine,
    RecordedPuzzleAdapter,
    Recording,
    RecordingProvenance,
    load_sample,
)
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import PooledStockfishAdapter

PREREGISTRATION = "8c02417"
DEST = FIXTURES / "recorded.json"


def save(recording: Recording, destination: Path) -> None:
    payload = json.dumps(recording.model_dump(exclude_none=True), separators=(",", ":")) + "\n"
    if len(payload.encode()) > 10_000_000:
        raise ValueError("Recording exceeds 10 MB size budget")
    with destination.open("x") as stream:
        stream.write(payload)


async def record() -> None:
    if DEST.exists():
        raise FileExistsError("Refusing to overwrite the frozen engine recording")
    puzzles = load_sample()
    results: list[PuzzleRecording | None] = [None] * len(puzzles)
    config = StockfishConfig()
    engine_names: set[str] = set()
    completed = 0

    async def worker(worker_index: int) -> None:
        nonlocal completed
        adapter = PooledStockfishAdapter(config)

        async def analyze(fen: str, nodes: int, multipv: int) -> EngineAnalysis:
            await adapter.reset()
            result = await adapter.analyze_position(fen, nodes=nodes, multipv=multipv)
            engine_names.add(str(result.config["engine_name"]))
            return result

        async def capture(puzzle: Puzzle) -> PuzzleRecording:
            before, after, san = puzzle.boards_and_san()
            source = await analyze(before.fen(), 10000, 1)
            reply = await analyze(after.fen(), 10000, 1)
            item = PuzzleRecording(
                puzzle_id=puzzle.puzzle_id,
                before=[RecordedLine.model_validate(line) for line in source.raw_result],
                after=[RecordedLine.model_validate(line) for line in reply.raw_result],
            )
            # Production candidate screening over these recorded fast requests.
            replay = RecordedPuzzleAdapter(puzzle, item)
            fast = await FastPassOrchestrator(replay, FastPassConfig(nodes=10000)).run_fast_pass(
                [san], puzzle.fen
            )
            if fast.candidates:
                focused = await analyze(after.fen(), 1000000, 4)
                item.focused = [RecordedLine.model_validate(line) for line in focused.raw_result]
            return item

        try:
            for index in range(worker_index, len(puzzles), 4):
                results[index] = await capture(puzzles[index])
                completed += 1
                if completed % 100 == 0:
                    print(f"Recorded {completed}/{len(puzzles)} puzzles", flush=True)
        finally:
            await adapter.quit()

    # TaskGroup cancels the other workers on any recording failure; their finally
    # blocks close their engines before this operation returns.
    async with asyncio.TaskGroup() as group:
        for worker_index in range(4):
            group.create_task(worker(worker_index))
    if len(engine_names) != 1 or any(item is None for item in results):
        raise ValueError("Recording incomplete or engines differ")
    provenance: dict[str, Any] = {
        "sample_sha256": "fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35",
        "preregistration_commit": PREREGISTRATION,
        "engine": next(iter(engine_names)),
        "threads": config.threads,
        "hash_mb": config.hash_size,
        "fast_nodes": 10000,
        "focused_nodes": 1000000,
        "focused_multipv": 4,
        "reset": "ucinewgame before every request",
    }
    recording = Recording(
        provenance=RecordingProvenance.model_validate(provenance),
        puzzles=[item for item in results if item is not None],
    )
    save(recording, DEST)
    print(f"Saved {len(recording.puzzles)} recordings to {DEST.name}", flush=True)


if __name__ == "__main__":
    asyncio.run(record())
