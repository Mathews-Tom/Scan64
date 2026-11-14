"""Record M48: uv run python scripts/record_diagnosis_reference.py SOURCE.pgn.zst.

The compressed source checksum is verified in code before streaming zstd.
--augment-before records missing deep source-position signals without redoing
the existing fast/focused output. The report never launches an engine.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

import chess.pgn

from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.learning.exercises.sound_moves import analyze_lesson_source
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import PooledStockfishAdapter

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests/fixtures/m48"
URL = "https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst"
SHA256 = "aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635"


@contextmanager
def verified_pgn(source: Path) -> Iterator[TextIO]:
    with source.open("rb") as archive:
        if hashlib.file_digest(archive, "sha256").hexdigest() != SHA256:
            raise ValueError("Compressed source does not match the published SHA256")
    with subprocess.Popen(["zstd", "-dc", str(source)], stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdout is not None
        try:
            yield proc.stdout
        finally:
            proc.stdout.close()
            proc.terminate()


def save_recording(data: dict[str, Any]) -> None:
    output = json.dumps(data, separators=(",", ":")) + "\n"
    if len(output.encode()) > 2_000_000:
        raise ValueError("Recorded engine output exceeds 2 MB")
    (DEST / "recorded.json").write_text(output)


async def augment_before(source: Path) -> None:
    # Verify the source even when resuming an existing recording.
    with verified_pgn(source) as stream:
        assert chess.pgn.read_game(stream) is not None
    data = json.loads((DEST / "recorded.json").read_text())
    config = StockfishConfig()
    requests = []
    for game in data["games"]:
        if game["id"] == "?":
            game["id"] = "reference_amateur_game"
        board = chess.Board()
        fens = [board.fen()]
        for san in game["moves"]:
            board.push_san(san)
            fens.append(board.fen())
        deep = game.setdefault("deep_before", {})
        for index in game["focused"]:
            if index not in deep:
                requests.append((deep, index, fens[int(index)]))
    data["provenance"].update(
        {
            "deep_before_nodes": 1_000_000,
            "deep_before_multipv": 4,
            "threads": config.threads,
            "hash_mb": config.hash_size,
        }
    )
    completed = 0

    async def worker(worker_index: int) -> None:
        nonlocal completed
        adapter = PooledStockfishAdapter(config)
        try:
            for deep, index, fen in requests[worker_index::4]:
                await adapter.reset()
                result = await adapter.analyze_position(fen, nodes=1_000_000, multipv=4)
                if result.config["engine_name"] != data["provenance"]["engine"]:
                    raise ValueError("Augmentation engine differs from the recorded engine")
                deep[index] = result.raw_result
                completed += 1
                if completed % 10 == 0:
                    save_recording(data)
                    print(f"Deep source positions: {completed}/{len(requests)}", flush=True)
        finally:
            await adapter.quit()

    await asyncio.gather(*(worker(index) for index in range(4)))
    save_recording(data)


async def record(source: Path, *, fresh80: bool = False) -> None:
    DEST.mkdir(exist_ok=True)
    selected = []
    with verified_pgn(source) as stream:
        while len(selected) < (120 if fresh80 else 40):
            game = chess.pgn.read_game(stream)
            if game is None:
                raise ValueError("Not enough qualifying games")
            rating_text = [game.headers.get(key, "0") for key in ("WhiteElo", "BlackElo")]
            if not all(value.isdecimal() for value in rating_text):
                continue
            ratings = [int(value) for value in rating_text]
            if not game.errors and all(600 <= elo <= 1600 for elo in ratings):
                if sum(1 for _ in game.mainline_moves()) >= 20:
                    selected.append(game)
    if fresh80:
        original = json.loads((DEST / "recorded.json").read_text())
        if [game.headers.get("Site") for game in selected[:40]] != [
            game["id"] for game in original["games"][:40]
        ]:
            raise ValueError("Verified archive prefix differs from original40")
        selected = selected[40:120]
    pgn = (
        "\n\n".join(
            game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
            for game in selected
        )
        + "\n"
    )
    (DEST / ("fresh80_real_games.pgn" if fresh80 else "real_games.pgn")).write_text(pgn)
    reference = None
    if not fresh80:
        with (ROOT / "tests/fixtures/reference_amateur_game.pgn").open() as stream:
            reference = chess.pgn.read_game(stream)
        assert reference is not None
    config = StockfishConfig()
    adapter = PooledStockfishAdapter(config)
    records: list[dict[str, Any]] = []
    engine_name = ""
    original_engine = (
        json.loads((DEST / "recorded.json").read_text())["provenance"]["engine"]
        if fresh80
        else None
    )
    try:
        for index, game in enumerate(selected if fresh80 else [*selected, reference]):
            moves = list(game.mainline_moves())
            board = game.board()
            sans = []
            fens = [board.fen()]
            for move in moves:
                sans.append(board.san(move))
                board.push(move)
                fens.append(board.fen())
            fast = []
            for fen in fens:
                await adapter.reset()
                analysis = await adapter.analyze_position(fen, nodes=10_000)
                fast.append(analysis.raw_result)
                engine_name = str(analysis.config["engine_name"])
                if fresh80 and engine_name != original_engine:
                    raise ValueError("Fresh recording engine differs from original engine")

            # Use the production candidate calculation, backed by the recorded fast pass.
            class Replay:
                async def analyze_position(self, fen: str, **kwargs: Any) -> Any:
                    from scan64.chess.analysis.models import EngineAnalysis

                    return EngineAnalysis(config={}, raw_result=fast[fens.index(fen)])

            fast_pass = await FastPassOrchestrator(
                Replay(), FastPassConfig(nodes=10_000, swing_threshold_cp=150)
            ).run_fast_pass(sans, game.headers.get("FEN"))
            candidates = fast_pass.candidates
            focused = {}
            deep_before = {}
            sources = {}
            for candidate in candidates:
                await adapter.reset()
                analysis = await adapter.analyze_position(candidate.fen, nodes=1_000_000, multipv=4)
                focused[str(candidate.move_index)] = analysis.raw_result
                await adapter.reset()
                before = await adapter.analyze_position(
                    fens[candidate.move_index], nodes=1_000_000, multipv=4
                )
                deep_before[str(candidate.move_index)] = before.raw_result
                if index == len(selected):
                    await adapter.reset()
                    source_analysis = await analyze_lesson_source(
                        adapter, fens[candidate.move_index]
                    )
                    sources[str(candidate.move_index)] = {
                        "raw_result": source_analysis.raw_result,
                        "config": source_analysis.config,
                    }
            records.append(
                {
                    "id": (
                        game.headers.get("Site")
                        if game.headers.get("Site") not in (None, "?")
                        else "reference_amateur_game"
                    ),
                    "moves": sans,
                    "fast": fast,
                    "focused": focused,
                    "deep_before": deep_before,
                    "sources": sources,
                }
            )
            print(
                f"Recorded {index + 1}/{len(selected)}: "
                f"{len(moves)} plies, {len(candidates)} candidates",
                flush=True,
            )
    finally:
        await adapter.quit()
    data = {
        "provenance": {
            "source_url": URL,
            "source_sha256": SHA256,
            "checksum_url": "https://database.lichess.org/standard/sha256sums.txt",
            "license": "CC0",
            "game_count": len(selected),
            "selection": (
                "Next 80 qualifying games after original40; qualifying ordinal 41..120."
                if fresh80
                else "First 40 qualifying games."
            ),
            "preregistration_commit": "c4490fa" if fresh80 else None,
            "engine": engine_name,
            "threads": config.threads,
            "hash_mb": config.hash_size,
            "fast_nodes": 10000,
            "focused_nodes": 1000000,
            "focused_multipv": 4,
            "deep_before_nodes": 1_000_000,
            "deep_before_multipv": 4,
            "reset": "ucinewgame before each position",
            "pgn_sha256": hashlib.sha256(pgn.encode()).hexdigest(),
        },
        "games": records,
    }
    if fresh80:
        output = json.dumps(data, separators=(",", ":")) + "\n"
        if len(output.encode()) > 8_000_000:
            raise ValueError("Fresh80 recording exceeds 8 MB")
        (DEST / "fresh80_recorded.json").write_text(output)
    else:
        save_recording(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--augment-before", action="store_true")
    parser.add_argument("--fresh80", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        augment_before(args.source)
        if args.augment_before
        else record(args.source, fresh80=args.fresh80)
    )
