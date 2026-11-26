"""Compare UCI engines on fixed positions at an equal node budget.

Usage:
    uv run python -m benchmarks.engines.compare_engines benchmarks/engines/engines.toml
    uv run python -m benchmarks.engines.compare_engines CONFIG --resume RESULTS.jsonl

Each (engine, position) search is appended to ``results_<UTC stamp>.jsonl`` as
soon as it finishes; ``--resume`` skips entries already present in that file.
A markdown summary is written next to it. Searches go through Scan64's own
line extraction (``scan64.providers.engine.uci.line_from_info``), so the
recorded fields are exactly what Scan64 would store.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chess
import chess.engine

from scan64.providers.engine.uci import lines_from_result

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("compare_engines")


@dataclass(frozen=True)
class EngineEntry:
    name: str
    binary_path: Path
    licence: str
    source: str


@dataclass(frozen=True)
class RunConfig:
    nodes: int
    multipv: int
    threads: int
    hash_mb: int
    results_dir: Path
    positions: tuple[tuple[str, str], ...]
    engines: tuple[EngineEntry, ...]


def load_config(path: Path) -> RunConfig:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    run = raw["run"]
    positions = tuple((entry["id"], entry["fen"]) for entry in raw["positions"])
    for position_id, fen in positions:
        if not chess.Board(fen).is_valid():
            raise ValueError(f"Position {position_id} is not a valid FEN: {fen}")
    engines = tuple(
        EngineEntry(
            name=entry["name"],
            binary_path=Path(os.path.expanduser(entry["binary_path"])),
            licence=entry["licence"],
            source=entry["source"],
        )
        for entry in raw["engines"]
    )
    return RunConfig(
        nodes=int(run["nodes"]),
        multipv=int(run["multipv"]),
        threads=int(run["threads"]),
        hash_mb=int(run["hash_mb"]),
        results_dir=REPO_ROOT / run["results_dir"],
        positions=positions,
        engines=engines,
    )


def done_keys(results: Path) -> set[tuple[str, str]]:
    if not results.exists():
        return set()
    keys = set()
    for line in results.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        keys.add((record["engine"], record["position"]))
    return keys


def append(results: Path, record: dict[str, Any]) -> None:
    with results.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


async def run_engine(
    entry: EngineEntry, config: RunConfig, results: Path, done: set[tuple[str, str]]
) -> None:
    if not entry.binary_path.is_file():
        LOG.warning("%s: binary not installed at %s", entry.name, entry.binary_path)
        append(results, {"engine": entry.name, "position": "*", "status": "not installed"})
        return
    _, engine = await chess.engine.popen_uci(str(entry.binary_path))
    try:
        declared = set(engine.options)
        options: dict[str, chess.engine.ConfigValue] = {}
        if "Threads" in declared:
            options["Threads"] = config.threads
        if "Hash" in declared:
            options["Hash"] = config.hash_mb
        if "UCI_ShowWDL" in declared:
            options["UCI_ShowWDL"] = True
        await engine.configure(options)
        multipv = config.multipv if "MultiPV" in declared else 1
        capabilities = {
            "id_name": engine.id.get("name"),
            "multipv_option": "MultiPV" in declared,
            "wdl_option": "UCI_ShowWDL" in declared,
            "strength_options": sorted(declared & {"Skill Level", "UCI_LimitStrength", "UCI_Elo"}),
        }
        for position_id, fen in config.positions:
            if (entry.name, position_id) in done:
                continue
            board = chess.Board(fen)
            result = await engine.analyse(
                board, chess.engine.Limit(nodes=config.nodes), multipv=multipv, game=object()
            )
            lines = lines_from_result(result, board.turn)
            record = {
                "engine": entry.name,
                "position": position_id,
                "status": "ok",
                "nodes_budget": config.nodes,
                "multipv_requested": config.multipv,
                "multipv_used": multipv,
                "capabilities": capabilities,
                "lines": lines,
            }
            append(results, record)
            LOG.info(
                "%s %s: %d lines, pv lengths %s",
                entry.name,
                position_id,
                len(lines),
                [len(line.get("pv", [])) for line in lines],
            )
    finally:
        await engine.quit()


def summarise(results: Path, config: RunConfig) -> str:
    records = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    rows = [
        "| Engine | Licence | MultiPV | WDL reported | Strength options | "
        + " | ".join(f"PV len `{position_id}`" for position_id, _ in config.positions)
        + " | Median PV len (all lines) | Median depth / seldepth |",
        "|" + "---|" * (7 + len(config.positions)),
    ]
    for entry in config.engines:
        mine = [record for record in records if record["engine"] == entry.name]
        if not mine or mine[0]["status"] != "ok":
            rows.append(
                f"| {entry.name} | {entry.licence} | not installed |"
                + " |" * (4 + len(config.positions))
            )
            continue
        by_position = {record["position"]: record for record in mine}
        capabilities = mine[0]["capabilities"]
        all_lines = [line for record in mine for line in record["lines"]]
        pv_lengths = [len(line.get("pv", [])) for line in all_lines]
        depths = [line["depth"] for line in all_lines if "depth" in line]
        seldepths = [line["seldepth"] for line in all_lines if "seldepth" in line]
        cells = []
        for position_id, _ in config.positions:
            record = by_position.get(position_id)
            cells.append(
                "/".join(str(len(line.get("pv", []))) for line in record["lines"])
                if record
                else "—"
            )
        wdl = any("wdl" in line for line in all_lines)
        rows.append(
            f"| {capabilities['id_name']} | {entry.licence} | "
            f"{'yes' if capabilities['multipv_option'] else 'no (1 line)'} | "
            f"{'yes' if wdl else 'no'} | "
            f"{', '.join(capabilities['strength_options']) or 'none'} | "
            + " | ".join(cells)
            + f" | {statistics.median(pv_lengths)} | "
            f"{statistics.median(depths) if depths else '—'} / "
            f"{statistics.median(seldepths) if seldepths else '—'} |"
        )
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--resume", type=Path, help="Existing results JSONL to continue")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    config.results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    results = args.resume or config.results_dir / f"results_{stamp}.jsonl"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.results_dir / f"compare_engines_{stamp}.log"),
        ],
    )
    done = done_keys(results)

    async def run_all() -> None:
        for entry in config.engines:
            await run_engine(entry, config, results, done)

    asyncio.run(run_all())
    summary = results.with_suffix(".md")
    summary.write_text(summarise(results, config), encoding="utf-8")
    LOG.info("Results: %s; summary: %s", results, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
