"""Run Scan64's evidence pipeline on the selected slow-loss games (issue #410).

Usage:
    uv run python -m benchmarks.factor_calibration.record_positions \\
        benchmarks/factor_calibration/games.json benchmarks/factor_calibration/positions.json

Input: ``games.json`` from ``select_games``. For every candidate and null move
the script runs two Stockfish searches (``NODES`` nodes, MultiPV ``MULTIPV``,
1 thread, 16 MB hash, a fresh process per search): before the move (the fast
analysis and the mate source) and after it (the reply and focused analysis).
Then it runs Scan64's own pipeline as the analysis job does:
``compose_candidate_evidence``, every registered detector and
``arbitrate_diagnoses``.

Kept positions (``set``):

- ``slow``: a candidate whose Stockfish swing is at least ``MIN_SWING_CP``;
- ``null``: a null move whose Stockfish swing is within ``+-NULL_SWING_CP`` and
  whose played move is not Stockfish's best move.

A position keeps its trusted diagnosis when arbitration gives one, else
``diagnosis`` is null and ``evidence`` holds the ``engine_analysis`` item, whose
payload carries the lines and scores the factor facts read. Analysis UUIDs are
UUID5 values from the position id and ``nps`` is dropped, so a rerun with the
same engine writes the same positions.

Provenance (rule 6): ``provenance`` records the commit, whether ``src`` or
``benchmarks`` had uncommitted changes (the output file excluded), and the
sha256 of this script, of ``games.json`` and of the search and selection
config; ``engine`` records the binary sha256 and, per network option, the
network name and its file sha256, or ``embedded`` when the network is built
into the binary (covered by ``binary_sha256``).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import chess

from benchmarks.narrator.build_positions import REPO_ROOT, _detectors
from benchmarks.narrator.build_positions_live import _stable_uuids
from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.composer import compose_candidate_evidence, needs_mate_source
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig
from scan64.providers.stockfish.identity import NETWORK_OPTIONS

STOCKFISH = Path("~/.local/bin/stockfish").expanduser()
NODES = 1_000_000
MULTIPV = 4
CONCURRENCY = 16
MIN_SWING_CP = 40
NULL_SWING_CP = 25
MATE_CP = 10_000


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _repo_label(path: Path) -> str:
    resolved = path.resolve()
    return (
        resolved.relative_to(REPO_ROOT).as_posix()
        if resolved.is_relative_to(REPO_ROOT)
        else path.name
    )


def search_config() -> dict[str, Any]:
    """Every setting that changes which positions are kept and what they hold."""
    return {
        "engine": {"nodes": NODES, "multipv": MULTIPV, "threads": 1, "hash_mb": 16},
        "selection": {"min_swing_cp": MIN_SWING_CP, "null_swing_cp": NULL_SWING_CP},
        "mate_cp": MATE_CP,
    }


def config_sha256(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()


def engine_manifest(identity: dict[str, Any]) -> dict[str, Any]:
    """Binary digest and per-network identity from the engine's analysis identity."""
    version = str(identity.get("engine_version") or "")
    files = json.loads(identity["network"]) if identity.get("network") else {}
    options = identity.get("options") or {}
    networks = {
        name: {"name": options[name], "sha256": files.get(name, "embedded")}
        for name in NETWORK_OPTIONS
        if isinstance(options.get(name), str) and options[name]
    }
    return {
        "binary_sha256": version.removeprefix("sha256:") if version.startswith("sha256:") else None,
        "network": networks,
    }


def code_provenance(games_path: Path, output: Path) -> dict[str, Any]:
    paths = ["src", "benchmarks"]
    resolved = output.resolve()
    if resolved.is_relative_to(REPO_ROOT):
        paths.append(f":(exclude){resolved.relative_to(REPO_ROOT).as_posix()}")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain", "--", *paths)),
        "script": _repo_label(Path(__file__)),
        "script_sha256": _file_sha256(Path(__file__)),
        "games_sha256": _file_sha256(games_path),
        "config_sha256": config_sha256(search_config()),
    }


def _cp(analysis: EngineAnalysis) -> int:
    """Top score, side to move's view; a mate counts as +-MATE_CP."""
    top = analysis.raw_result[0]
    if top.get("score_mate") is not None:
        return MATE_CP if top["score_mate"] > 0 else -MATE_CP
    return int(top["score_cp"])


async def _position(
    adapter: StockfishAdapter,
    gate: asyncio.Semaphore,
    detectors: tuple[PatternDetector, ...],
    game: dict[str, Any],
    index: int,
    kind: str,
) -> dict[str, Any] | None:
    board = chess.Board()
    for san in game["moves"][:index]:
        board.push_san(san)
    before = board.copy(stack=False)
    played = game["moves"][index]
    after = before.copy(stack=False)
    after.push_san(played)
    if after.is_game_over():
        return None
    async with gate:
        before_analysis = await adapter.analyze_position(before.fen(), nodes=NODES, multipv=MULTIPV)
    async with gate:
        after_analysis = await adapter.analyze_position(after.fen(), nodes=NODES, multipv=MULTIPV)
    swing_cp = _cp(before_analysis) + _cp(after_analysis)
    best = before_analysis.raw_result[0]["pv"][0]
    played_uci = before.parse_san(played).uci()
    if kind == "slow" and swing_cp < MIN_SWING_CP:
        return None
    if kind == "null" and (abs(swing_cp) > NULL_SWING_CP or best == played_uci):
        return None
    position_id = f"lichess:{game['id']}:{index}"
    evidence = compose_candidate_evidence(
        before_board=before,
        after_board=after,
        history_san=game["moves"][: index + 1],
        initial_fen=None,
        position_id=position_id,
        fast_analysis=before_analysis,
        reply_analysis=after_analysis,
        focused_analysis=after_analysis,
        mate_source_analysis=before_analysis if needs_mate_source(after_analysis) else None,
        played_move=played,
        swing_cp=swing_cp,
    )
    opportunity = LearningOpportunity(
        opportunity_id=position_id,
        position_id=position_id,
        player_id="calibration",
        played_move=played,
        engine_eval_before=0.0,
        engine_eval_after=-(swing_cp / 100.0),
    )
    detected = []
    for detector in detectors:
        detected.extend(
            await detector.detect(opportunity, evidence, PlayerContext(player_id="calibration"))
        )
    selection = arbitrate_diagnoses(detected, evidence)
    renamed = {item.evidence_id: f"{position_id}_ev{n}" for n, item in enumerate(evidence)}
    diagnosis = None
    kept = [evidence[0]]
    if selection is not None:
        primary, secondary = selection
        diagnosis = {
            "primary": primary.skill_id,
            "secondary": [item.skill_id for item in secondary],
            "confidence": primary.confidence,
            "evidence_refs": [renamed[ref] for ref in primary.evidence_ids],
        }
        kept = [item for item in evidence if item.evidence_id in primary.evidence_ids]
    record = {
        "position_id": position_id,
        "set": kind,
        "game_id": game["url"],
        "move_index": index,
        "fen": before.fen(),
        "played_move": played,
        "stockfish_swing_cp": swing_cp,
        "diagnosis": diagnosis,
        "evidence": [
            {**item.model_dump(mode="json"), "evidence_id": renamed[item.evidence_id]}
            for item in kept
        ],
    }
    return _stable_uuids(record, position_id)


async def record(games_path: Path, output: Path) -> None:
    data = json.loads(games_path.read_text(encoding="utf-8"))
    provenance = code_provenance(games_path, output)
    adapter = StockfishAdapter(StockfishConfig(binary_path=str(STOCKFISH), threads=1, hash_size=16))
    identity = await adapter.analysis_identity()
    gate = asyncio.Semaphore(CONCURRENCY)
    detectors = _detectors()
    jobs = [
        _position(adapter, gate, detectors, game, index, kind)
        for game in data["games"]
        for kind, indices in (("slow", game["candidates"]), ("null", game["nulls"]))
        for index in indices
    ]
    positions = [found for found in await asyncio.gather(*jobs) if found is not None]
    output.write_text(
        json.dumps(
            {
                "source": "Lichess games lost slowly, Scan64 evidence and diagnosis pipeline",
                "games": games_path.resolve().relative_to(REPO_ROOT).as_posix(),
                "provenance": provenance,
                "engine": {
                    "id_name": identity.get("engine_name"),
                    **search_config()["engine"],
                    **engine_manifest(identity),
                },
                "selection": search_config()["selection"],
                "counts": {
                    kind: sum(p["set"] == kind for p in positions) for kind in ("slow", "null")
                },
                "diagnosed": sum(p["diagnosis"] is not None for p in positions),
                "positions": positions,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("games", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(record(args.games, args.output))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
