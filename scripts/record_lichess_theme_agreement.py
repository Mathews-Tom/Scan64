"""Record M77 once, after approval of the committed pre-registration.

Run: uv run python scripts/record_lichess_theme_agreement.py
This explicit operation launches Stockfish; the offline scorer never does.
The output refuses overwrite. All engines are closed on success or failure.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.learning.evaluation.lichess_themes import (
    FIXTURES,
    ORIGINAL_RECORDING_SHA256,
    SOURCE_RECORDING_NAME,
    V3_PREREGISTRATION,
    Puzzle,
    PuzzleRecording,
    RecordedLine,
    RecordedPuzzleAdapter,
    Recording,
    RecordingProvenance,
    SourceAnalysisConfig,
    SourcePuzzleRecording,
    SourceRecording,
    SourceRecordingProvenance,
    load_sample,
    needs_source_recording,
    source_analysis_id,
    validate_source_recording,
)
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import PooledStockfishAdapter

PREREGISTRATION = "8c02417"
DEST = FIXTURES / "recorded.json"


def save(recording: Recording | SourceRecording, destination: Path) -> None:
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


async def record_source() -> None:
    """Add only preregistered deep source requests; never regenerate v1 evidence."""
    destination = FIXTURES / SOURCE_RECORDING_NAME
    if destination.exists():
        raise FileExistsError("Refusing to overwrite the frozen v3 source recording")
    payload = DEST.read_bytes()
    if hashlib.sha256(payload).hexdigest() != ORIGINAL_RECORDING_SHA256:
        raise ValueError("Frozen original recording checksum mismatch")
    original = Recording.model_validate_json(payload)
    puzzles = load_sample()
    if [item.puzzle_id for item in original.puzzles] != [p.puzzle_id for p in puzzles]:
        raise ValueError("Original recording IDs differ from the frozen sample")
    eligible = []
    for puzzle, item in zip(puzzles, original.puzzles, strict=True):
        if not needs_source_recording(item):
            continue
        _, _, san = puzzle.boards_and_san()
        fast = await FastPassOrchestrator(
            RecordedPuzzleAdapter(puzzle, item), FastPassConfig(nodes=10000)
        ).run_fast_pass([san], puzzle.fen)
        if not fast.candidates:
            raise ValueError("Source eligibility differs from recorded fast-pass screening")
        eligible.append(puzzle)
    results: list[SourcePuzzleRecording | None] = [None] * len(eligible)
    config = StockfishConfig()
    completed = 0

    async def worker(worker_index: int) -> None:
        nonlocal completed
        adapter = PooledStockfishAdapter(config)
        try:
            for index in range(worker_index, len(eligible), 4):
                puzzle = eligible[index]
                before, _, _ = puzzle.boards_and_san()
                await adapter.reset()
                analysis = await adapter.analyze_position(before.fen(), nodes=1000000, multipv=4)
                if analysis.config["engine_name"] != original.provenance.engine:
                    raise ValueError("Source engine differs from the frozen original engine")
                results[index] = SourcePuzzleRecording(
                    puzzle_id=puzzle.puzzle_id,
                    source_fen=before.fen(),
                    analysis_id=source_analysis_id(puzzle),
                    config=SourceAnalysisConfig.model_validate(analysis.config),
                    lines=[RecordedLine.model_validate(line) for line in analysis.raw_result],
                )
                completed += 1
                if completed % 50 == 0:
                    print(f"Recorded {completed}/{len(eligible)} deep sources", flush=True)
        finally:
            await adapter.quit()

    async with asyncio.TaskGroup() as group:
        for worker_index in range(4):
            group.create_task(worker(worker_index))
    if any(item is None for item in results):
        raise ValueError("Source recording incomplete")
    recording = SourceRecording(
        provenance=SourceRecordingProvenance(
            sample_sha256=original.provenance.sample_sha256,
            original_recording_sha256=ORIGINAL_RECORDING_SHA256,
            preregistration_commit=V3_PREREGISTRATION,
            engine=original.provenance.engine,
            threads=config.threads,
            hash_mb=config.hash_size,
            source_nodes=1000000,
            source_multipv=4,
            reset="ucinewgame before every request",
            eligibility="fast-pass candidate with positive reply mate in 1-3",
        ),
        puzzles=[item for item in results if item is not None],
    )
    validate_source_recording(puzzles, original, recording)
    save(recording, destination)
    print(f"Saved {len(recording.puzzles)} source analyses to {destination.name}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=("v1", "v3"), default="v1")
    arguments = parser.parse_args()
    asyncio.run(record_source() if arguments.version == "v3" else record())
