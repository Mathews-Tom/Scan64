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
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO, cast

import chess.pgn

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.learning.evaluation import m94_protocol
from scan64.learning.evaluation.m93_protocol import PROTOCOL, protocol_digest
from scan64.learning.exercises.sound_moves import analyze_lesson_source
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import PooledStockfishAdapter

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests/fixtures/m48"
URL = "https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst"
SHA256 = "aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635"


@contextmanager
def verified_pgn(source: Path) -> Iterator[TextIO]:
    """Decompress a verified private snapshot, not the mutable source archive."""
    with tempfile.TemporaryFile(mode="w+b") as archive:
        digest = hashlib.sha256()
        with source.open("rb") as original:
            while chunk := original.read(1024 * 1024):
                archive.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != SHA256:
            raise ValueError("Compressed source does not match the published SHA256")
        archive.seek(0)
        with subprocess.Popen(
            ["zstd", "-dc"], stdin=archive, stdout=subprocess.PIPE, text=True
        ) as proc:
            assert proc.stdout is not None
            try:
                yield cast(TextIO, proc.stdout)
            finally:
                proc.stdout.close()
                proc.terminate()


def save_recording(data: dict[str, Any]) -> None:
    output = (json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8")
    if len(output) > 2_000_000:
        raise ValueError("Recorded engine output exceeds 2 MB")
    (DEST / "recorded.json").write_bytes(output)


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


async def finalize_m94_manifest(
    preregistration_commit: str, recording_sha256: str, pgn_sha256: str
) -> None:
    """Finalize complete, digest-bound outputs without opening an engine or source."""
    protocol = m94_protocol.validate_frozen_sources(preregistration_commit, ROOT)
    prefix = m94_protocol.PREFIX
    manifest_path = DEST / f"{prefix}_manifest.json"
    if manifest_path.exists():
        raise ValueError("Refusing to overwrite an M94 manifest")
    recording_bytes = (DEST / f"{prefix}_recorded.json").read_bytes()
    pgn_bytes = (DEST / f"{prefix}_real_games.pgn").read_bytes()
    freeze_sha256 = hashlib.sha256(m94_protocol.FREEZE.read_bytes()).hexdigest()
    digest = m94_protocol.protocol_digest(protocol)
    manifest = {
        "preregistration_commit": preregistration_commit,
        "protocol_sha256": digest,
        "source_revisions_sha256": freeze_sha256,
        "recording_sha256": recording_sha256,
        "pgn_sha256": pgn_sha256,
    }
    identity = m94_protocol.MeasurementIdentity(
        preregistration_commit=preregistration_commit,
        recording_sha256=recording_sha256,
        pgn_sha256=pgn_sha256,
        manifest_sha256="",  # Finalization creates these bytes; not a replay identity.
        protocol_sha256=digest,
        source_revisions_sha256=freeze_sha256,
    )
    prior_paths = tuple(
        DEST / name
        for name in (
            "recorded.json",
            "fresh80_recorded.json",
            "m79_fresh80_recorded.json",
            "m93_fresh80_recorded.json",
            "m94_fresh80_recorded.json",
        )
    )
    corpus = m94_protocol.validate_artifacts(
        recording_bytes, pgn_bytes, prior_paths, identity, manifest
    )
    # Recalculate only candidate selection from saved fast output, never labels/search.
    from scan64.learning.evaluation.real_games import RecordedAdapter

    for game in corpus["games"]:
        fast = await FastPassOrchestrator(
            RecordedAdapter(game), FastPassConfig(nodes=10_000, swing_threshold_cp=150)
        ).run_fast_pass(game["moves"])
        if {str(candidate.move_index) for candidate in fast.candidates} != game["focused"].keys():
            raise ValueError("M94 recording is incomplete; STOP without another search")
    # Publish a fully closed manifest exclusively, leaving no partial target.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=DEST, prefix=".m94-manifest-", delete=False
        ) as out:
            temporary = Path(out.name)
            out.write(json.dumps(manifest, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, manifest_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _select_games(
    source: Path, *, fresh80: bool, m79_fresh80: bool, m93_fresh80: bool, m94_v2_fresh80: bool
) -> list[chess.pgn.Game]:
    """Select only the preregistered slice after verifying every exposed prefix."""
    selected: list[chess.pgn.Game] = []
    with verified_pgn(source) as stream:
        count = (
            440
            if m94_v2_fresh80
            else 280
            if m93_fresh80
            else 200
            if m79_fresh80
            else 120
            if fresh80
            else 40
        )
        while len(selected) < count:
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
        if m79_fresh80 or m93_fresh80 or m94_v2_fresh80:
            previous = json.loads((DEST / "fresh80_recorded.json").read_text())
            if [game.headers.get("Site") for game in selected[40:120]] != [
                game["id"] for game in previous["games"]
            ]:
                raise ValueError("Verified archive prefix differs from M78 fresh80")
        if m93_fresh80 or m94_v2_fresh80:
            previous = json.loads((DEST / "m79_fresh80_recorded.json").read_text())
            if [game.headers.get("Site") for game in selected[120:200]] != [
                game["id"] for game in previous["games"]
            ]:
                raise ValueError("Verified archive prefix differs from M79 fresh80")
        if m94_v2_fresh80:
            previous = json.loads((DEST / "m93_fresh80_recorded.json").read_text())
            if [game.headers.get("Site") for game in selected[200:280]] != [
                game["id"] for game in previous["games"]
            ]:
                raise ValueError("Verified archive prefix differs from M93 fresh80")
            previous = json.loads((DEST / "m94_fresh80_recorded.json").read_bytes())
            if [game.headers.get("Site") for game in selected[280:360]] != [
                game["id"] for game in previous["games"]
            ]:
                raise ValueError("Verified archive prefix differs from archived M94 v1")
        start = 360 if m94_v2_fresh80 else 200 if m93_fresh80 else 120 if m79_fresh80 else 40
        selected = selected[start : start + 80]
        if m94_v2_fresh80:
            selected_ids = [game.headers.get("Site") for game in selected]
            prior_ids = {
                game["id"]
                for name in (
                    "recorded.json",
                    "fresh80_recorded.json",
                    "m79_fresh80_recorded.json",
                    "m93_fresh80_recorded.json",
                    "m94_fresh80_recorded.json",
                )
                for game in json.loads((DEST / name).read_text())["games"]
            }
            if len(set(selected_ids)) != 80 or prior_ids.intersection(selected_ids):
                raise ValueError("M94 selection contains duplicate or exposed identities")
    return selected


async def record(
    source: Path,
    *,
    fresh80: bool = False,
    m79_fresh80: bool = False,
    m93_fresh80: bool = False,
    m94_v2_fresh80: bool = False,
    preregistration_commit: str | None = None,
) -> None:
    DEST.mkdir(exist_ok=True)
    fresh80 = fresh80 or m79_fresh80 or m93_fresh80 or m94_v2_fresh80
    prefix = (
        m94_protocol.PREFIX
        if m94_v2_fresh80
        else "m93_fresh80"
        if m93_fresh80
        else "m79_fresh80"
        if m79_fresh80
        else "fresh80"
    )
    preregistration = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if m79_fresh80 or m93_fresh80
        else "c4490fa"
    )
    if (m79_fresh80 or m93_fresh80 or m94_v2_fresh80) and any(
        (DEST / f"{prefix}_{suffix}").exists()
        for suffix in ("recorded.json", "real_games.pgn", "manifest.json")
    ):
        raise ValueError("Refusing to overwrite a consumed holdout")
    protocol: dict[str, Any] | None = None
    if m94_v2_fresh80:
        if preregistration_commit is None:
            raise ValueError("M94 requires an explicit preregistration commit SHA")
        protocol = m94_protocol.validate_frozen_sources(preregistration_commit, ROOT)
        preregistration = preregistration_commit
    if m93_fresh80:
        # Verify frozen implementations before selecting or searching any new position.
        frozen = [
            *PROTOCOL["revisions"],
            "scripts/record_diagnosis_reference.py",
            "src/scan64/learning/evaluation/m93_protocol.py",
        ]
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *frozen], cwd=ROOT, check=True)
        for name, digest in PROTOCOL["revisions"].items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Frozen M93 implementation changed: {name}")
    original_engine = (
        json.loads((DEST / "recorded.json").read_text())["provenance"]["engine"]
        if fresh80
        else None
    )
    config = StockfishConfig()
    if (m93_fresh80 or m94_v2_fresh80) and (config.threads != 1 or config.hash_size != 16):
        raise ValueError("M93/M94 require one thread and 16 MB hash")
    adapter = PooledStockfishAdapter(config)
    records: list[dict[str, Any]] = []
    engine_name = ""
    try:
        if m94_v2_fresh80:
            assert protocol is not None
            identity = await adapter.analysis_identity()
            if identity.get("engine_name") != protocol["engine"]:
                raise ValueError("M94 requires the preregistered Stockfish 19 engine")
            if identity.get("engine_name") != original_engine:
                raise ValueError("Fresh recording engine differs from original engine")
        selected = _select_games(
            source,
            fresh80=fresh80,
            m79_fresh80=m79_fresh80,
            m93_fresh80=m93_fresh80,
            m94_v2_fresh80=m94_v2_fresh80,
        )
        pgn = (
            "\n\n".join(
                game.accept(
                    chess.pgn.StringExporter(headers=True, variations=False, comments=False)
                )
                for game in selected
            )
            + "\n"
        ).encode("utf-8")
        pgn_path = DEST / (f"{prefix}_real_games.pgn" if fresh80 else "real_games.pgn")
        reference = None
        if not fresh80:
            with (ROOT / "tests/fixtures/reference_amateur_game.pgn").open() as stream:
                reference = chess.pgn.read_game(stream)
            assert reference is not None
        # Wrong configuration or identity must not consume the one-shot holdout.
        with pgn_path.open("xb" if m93_fresh80 or m94_v2_fresh80 else "wb") as output_pgn:
            output_pgn.write(pgn)
        for index, game in enumerate(
            selected if fresh80 else [*selected, cast(chess.pgn.Game, reference)]
        ):
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
                if m94_v2_fresh80 and engine_name != cast(dict[str, Any], protocol)["engine"]:
                    raise ValueError("M94 requires the preregistered Stockfish 19 engine")

            # Use the production candidate calculation, backed by the recorded fast pass.
            class Replay:
                async def analyze_position(
                    self,
                    fen: str,
                    nodes: int | None = None,
                    depth: int | None = None,
                    time_ms: int | None = None,
                    multipv: int = 1,
                ) -> EngineAnalysis:
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
    data: dict[str, Any] = {
        "provenance": {
            "source_url": URL,
            "source_sha256": SHA256,
            "checksum_url": "https://database.lichess.org/standard/sha256sums.txt",
            "license": "CC0",
            "game_count": len(selected),
            "selection": (
                cast(dict[str, Any], protocol)["selection"]
                if m94_v2_fresh80
                else PROTOCOL["selection"]
                if m93_fresh80
                else "Next 80 qualifying games after original40 and M78 fresh80; ordinal 121..200."
                if m79_fresh80
                else "Next 80 qualifying games after original40; qualifying ordinal 41..120."
                if fresh80
                else "First 40 qualifying games."
            ),
            "preregistration_commit": preregistration if fresh80 else None,
            "engine": engine_name,
            "threads": config.threads,
            "hash_mb": config.hash_size,
            "fast_nodes": 10000,
            "focused_nodes": 1000000,
            "focused_multipv": 4,
            "deep_before_nodes": 1_000_000,
            "deep_before_multipv": 4,
            "reset": "ucinewgame before each position",
            "pgn_sha256": hashlib.sha256(pgn).hexdigest(),
        },
        "games": records,
    }
    if m93_fresh80:
        data["provenance"].update({"protocol": PROTOCOL, "protocol_sha256": protocol_digest()})
    if m94_v2_fresh80:
        assert protocol is not None
        data["provenance"].update(
            {"protocol": protocol, "protocol_sha256": m94_protocol.protocol_digest(protocol)}
        )
    if fresh80:
        output = (json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8")
        if len(output) > 8_000_000:
            raise ValueError("Fresh80 recording exceeds 8 MB")
        with (DEST / f"{prefix}_recorded.json").open(
            "xb" if m93_fresh80 or m94_v2_fresh80 else "wb"
        ) as output_file:
            output_file.write(output)
        if m94_v2_fresh80:
            recording_sha256 = hashlib.sha256(output).hexdigest()
            pgn_sha256 = hashlib.sha256(pgn).hexdigest()
            # A failed status pipe must not strand complete outputs without a manifest.
            try:
                print(
                    f"Complete M94 v2 outputs: recording_sha256={recording_sha256} "
                    f"pgn_sha256={pgn_sha256} preregistration_commit={preregistration}",
                    flush=True,
                )
            finally:
                await finalize_m94_manifest(preregistration, recording_sha256, pgn_sha256)
    else:
        save_recording(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--augment-before", action="store_true")
    mode.add_argument("--fresh80", action="store_true")
    mode.add_argument("--m79-fresh80", action="store_true")
    mode.add_argument("--m93-fresh80", action="store_true")
    mode.add_argument("--m94-v2-fresh80", action="store_true")
    mode.add_argument("--finalize-m94-manifest", action="store_true")
    parser.add_argument("--preregistration-commit")
    parser.add_argument("--recording-sha256")
    parser.add_argument("--pgn-sha256")
    args = parser.parse_args()
    if args.finalize_m94_manifest:
        if args.source is not None or not all(
            (args.preregistration_commit, args.recording_sha256, args.pgn_sha256)
        ):
            parser.error(
                "Finalization requires preregistration/recording/PGN digests and no source"
            )
        asyncio.run(
            finalize_m94_manifest(
                args.preregistration_commit, args.recording_sha256, args.pgn_sha256
            )
        )
    else:
        if args.source is None:
            parser.error("Recording requires a source archive")
        if args.m94_v2_fresh80 and not args.preregistration_commit:
            parser.error("M94 v2 requires --preregistration-commit")
        if args.recording_sha256 or args.pgn_sha256:
            parser.error("Artifact digests are only accepted for finalization")
        asyncio.run(
            augment_before(args.source)
            if args.augment_before
            else record(
                args.source,
                fresh80=args.fresh80,
                m79_fresh80=args.m79_fresh80,
                m93_fresh80=args.m93_fresh80,
                m94_v2_fresh80=args.m94_v2_fresh80,
                preregistration_commit=args.preregistration_commit,
            )
        )
