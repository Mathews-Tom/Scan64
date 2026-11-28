"""Variant engine probe: MultiPV lines on every python-chess variant board.

Usage:
    uv run python -m benchmarks.engines.variants_probe benchmarks/engines/variants_probe_linux.toml
    uv run python -m benchmarks.engines.variants_probe --summarise RESULTS.jsonl

For each variant Scan64 plans to support (#398) the probe analyses the start
position and two or three fixed positions (``POSITIONS``) with the engine of
one profile: MultiPV from the config, a fixed node budget, one thread, a fresh
game (``ucinewgame``) per position.

The probe never sets ``UCI_Variant``. python-chess selects it from the board
type on every search (``CrazyhouseBoard`` -> ``crazyhouse``) and refuses a
manual setting. Before a search the probe reads the values the engine declares
for ``UCI_Variant``; a variant the engine does not list, or an engine with no
``UCI_Variant`` option, is recorded as ``not declared`` and not searched.

Every PV move is checked against the python-chess variant rules on the raw
``info ... pv`` tokens the engine sent, not on python-chess's parsed line:
python-chess drops the whole ``pv`` of an ``info`` line whose moves it cannot
play, and its result then keeps the previous line's PV.

One record per position is appended to ``variants_probe_<UTC stamp>.jsonl``.
A run manifest, ``<results>.manifest.json``, is written before the first
search: the commit and dirty flag (whole tree, results directory excluded),
the engine's name, executable and network digests and option values, the
declared ``UCI_Variant`` values, the config and profile digests, the settings
with every position, their digest (``config_digest``) and the python-chess
version. Its digest, ``run_id``, is stored in every record. A run always
writes new files; there is no resume. ``--summarise RESULTS`` rewrites the
markdown summary from the manifest and records alone, re-checking every PV's
legality from the recorded FEN and moves.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chess
import chess.engine
import chess.variant

from scan64.providers.engine.config import EngineConfig, EngineProfile, load_engine_profile
from scan64.providers.engine.uci import line_from_info, startup_options
from scan64.providers.stockfish.identity import engine_identity

REPO_ROOT = Path(__file__).resolve().parents[2]
THREADS = 1
# Recorded only: the probe drives one engine process directly, not the pool.
POOL_ENV = "SCAN64_ENGINE_POOL_BATCH_CONCURRENCY"

BOARDS: dict[str, type[chess.Board]] = {
    board.uci_variant: board
    for board in (
        chess.variant.CrazyhouseBoard,
        chess.variant.ThreeCheckBoard,
        chess.variant.AtomicBoard,
        chess.variant.AntichessBoard,
        chess.variant.KingOfTheHillBoard,
        chess.variant.HordeBoard,
        chess.variant.RacingKingsBoard,
    )
}


@dataclass(frozen=True)
class ProbePosition:
    variant: str  # python-chess ``uci_variant`` of the board
    label: str
    fen: str
    note: str

    def board(self) -> chess.Board:
        return BOARDS[self.variant](self.fen)


def _start(variant: str) -> ProbePosition:
    return ProbePosition(variant, "start", BOARDS[variant].starting_fen, "start position")


POSITIONS: tuple[ProbePosition, ...] = (
    _start("crazyhouse"),
    ProbePosition(
        "crazyhouse",
        "fried-liver",
        "r1bq1b1r/ppp2kpp/2n5/3np3/2B5/8/PPPP1PPP/RNBQK2R[PPnp] w KQ - 0 7",
        "1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 4.Ng5 d5 5.exd5 Nxd5 6.Nxf7 Kxf7; "
        "in hand: white P P, black n p",
    ),
    ProbePosition(
        "crazyhouse",
        "alekhine",
        "r2qkb1r/pppbpppp/8/3n4/3P4/8/PPP2PPP/RNBQKB1R[NPnp] w KQkq - 0 7",
        "1.e4 Nf6 2.e5 Nd5 3.d4 d6 4.Nf3 dxe5 5.Nxe5 Nd7 6.Nxd7 Bxd7; "
        "in hand: white N P, black n p",
    ),
    ProbePosition(
        "crazyhouse",
        "scandinavian",
        "rn2kbnr/ppq2ppp/2p1p3/8/3P4/2N2B2/PPP2PPP/R1BQ1RK1[BPnp] w kq - 1 9",
        "Scandinavian after 8...Qc7; in hand: white B P, black n p",
    ),
    _start("3check"),
    ProbePosition(
        "3check",
        "white-two-checks",
        "r1bq1bnr/pppp1k1p/2n3p1/4Q3/4P3/8/PPPP1PPP/RNB1K1NR b KQ - 1+3 0 5",
        "1.e4 e5 2.Bc4 Nc6 3.Bxf7+ Kxf7 4.Qh5+ g6 5.Qxe5; white has one check left",
    ),
    ProbePosition(
        "3check",
        "black-two-checks",
        "rn2kbnr/ppp2ppp/4p3/6B1/3P4/8/PPP1NPPP/R2QK2R b KQkq - 3+1 1 8",
        "black gave Qe5+ and Qxe2+ and has one check left: ...Bb4+ wins",
    ),
    _start("atomic"),
    ProbePosition(
        "atomic",
        "knight-threat",
        "rnbqkb1r/ppp1pppp/5n2/3pN3/8/8/PPPPPPPP/RNBQKB1R w KQkq - 2 3",
        "1.Nf3 d5 2.Ne5 Nf6: Nxf7 explodes the king on e8",
    ),
    ProbePosition(
        "atomic",
        "pinned-knight",
        "rnbqk2r/pppp1ppp/4pn2/8/1b6/2N1P2N/PPPP1PPP/R1BQKB1R w KQkq - 4 4",
        "1.e3 e6 2.Nh3 Bb4 3.Nc3 Nf6",
    ),
    _start("antichess"),
    ProbePosition(
        "antichess",
        "forced-capture",
        "rnbqkbnr/p1pppppp/8/1p6/8/4P3/PPPP1PPP/RNBQKBNR w - - 0 2",
        "1.e3 b5: Bxb5 is the only legal move",
    ),
    ProbePosition(
        "antichess",
        "middlegame",
        "r2qkbnr/p1pp1ppp/np2p3/8/8/1P2P2N/P1PP1PPP/RNBQK2R w - - 0 5",
        "1.e3 e6 2.Nh3 b6 3.b3 Ba6 4.Bxa6 Nxa6",
    ),
    _start("kingofthehill"),
    ProbePosition(
        "kingofthehill",
        "king-walk",
        "rnbq1b1r/ppppkppp/5n2/4p3/4P3/4K3/PPPP1PPP/RNBQ1BNR w - - 4 4",
        "1.e4 e5 2.Ke2 Ke7 3.Ke3 Nf6",
    ),
    ProbePosition(
        "kingofthehill",
        "italian",
        "r1bqk1nr/ppp2ppp/2np4/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 0 5",
        "1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.d3 d6",
    ),
    _start("horde"),
    ProbePosition(
        "horde",
        "opening",
        "rnbqkbnr/p1p3pp/2p1p3/5PP1/PPP1PPPP/PPPPPPPP/PPPPPPPP/PPPPPPPP w kq - 0 5",
        "1.d5 e6 2.dxe6 fxe6 3.c6 bxc6 4.bxc6 dxc6",
    ),
    ProbePosition(
        "horde",
        "pawn-wedge",
        "r1bqkbnr/pppp1p1p/2n1pP2/1PP1P3/PPPP1PPP/PPPPPPPP/PPPPPPPP/PPPPPPPP w kq - 1 4",
        "1.f6 gxf6 2.gxf6 e6 3.e5 Nc6",
    ),
    _start("racingkings"),
    ProbePosition(
        "racingkings",
        "race",
        "8/8/8/8/1k4K1/8/1rbnNBR1/qrbnNBRQ w - - 4 3",
        "1.Kh3 Ka3 2.Kg4 Kb4",
    ),
    ProbePosition(
        "racingkings",
        "knights-out",
        "8/8/8/8/3Nn2K/k7/1rb2BR1/qrbnNBRQ b - - 5 3",
        "1.Kh3 Ka3 2.Nd4 Ne4 3.Kh4",
    ),
)


def validate_positions(positions: Iterable[ProbePosition]) -> None:
    """Every FEN round-trips, is valid and leaves the side to move a legal move."""
    for position in positions:
        board = position.board()
        where = f"{position.variant}/{position.label}"
        if board.fen() != position.fen:
            raise ValueError(f"{where}: FEN does not round-trip: {board.fen()}")
        if not board.is_valid():
            raise ValueError(f"{where}: invalid position ({board.status()!r})")
        if board.is_game_over():
            raise ValueError(f"{where}: the game is already over")


@dataclass(frozen=True)
class ProbeConfig:
    path: Path
    results_dir: Path
    profile: Path
    nodes: int
    multipv: int


def _repo_path(raw: str) -> Path:
    """Absolute (``~`` expands) or relative to the repository root."""
    path = Path(os.path.expanduser(raw))
    return path if path.is_absolute() else REPO_ROOT / path


def shown(path: Path) -> str:
    """Repository-relative when inside the repository, so reports carry no home path."""
    resolved = path.resolve()
    return str(resolved.relative_to(REPO_ROOT)) if resolved.is_relative_to(REPO_ROOT) else path.name


def _positive(run: dict[str, Any], key: str) -> int:
    value = run.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"run.{key} must be a positive integer")
    return value


def load_config(path: Path) -> ProbeConfig:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    run = raw["run"]
    unknown = sorted(set(run) - {"results_dir", "profile", "nodes", "multipv"})
    if unknown:
        raise ValueError(f"unknown run keys: {', '.join(unknown)}")
    return ProbeConfig(
        path=path,
        results_dir=_repo_path(run["results_dir"]),
        profile=_repo_path(run["profile"]),
        nodes=_positive(run, "nodes"),
        multipv=_positive(run, "multipv"),
    )


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def code_provenance(results_dir: Path) -> dict[str, Any]:
    """The commit and whether the tree differs from it, results directory excluded."""
    paths = ["."]
    resolved = results_dir.resolve()
    if resolved.is_relative_to(REPO_ROOT):
        paths.append(f":(exclude){resolved.relative_to(REPO_ROOT).as_posix()}")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain", "--", *paths)),
    }


def cpu_affinity() -> str | None:
    """CPUs this process may run on (``taskset``), as ranges: ``32-39``."""
    if not hasattr(os, "sched_getaffinity"):
        return None
    cpus = sorted(os.sched_getaffinity(0))
    ranges: list[str] = []
    start = previous = cpus[0]
    for cpu in [*cpus[1:], None]:
        if cpu is not None and cpu == previous + 1:
            previous = cpu
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        if cpu is not None:
            start = previous = cpu
    return ",".join(ranges)


class RecordingUci(chess.engine.UciProtocol):
    """A UCI process that keeps every line the engine sends, for the raw PV check."""

    def __init__(self) -> None:
        super().__init__()
        self.received: list[str] = []

    def _line_received(self, line: str) -> None:
        self.received.append(line)
        super()._line_received(line)


async def start_engine(binary_path: str) -> RecordingUci:
    """``chess.engine.popen_uci`` with the recording protocol."""
    transport, engine = await RecordingUci.popen(binary_path)
    try:
        await engine.initialize()
    except BaseException:
        transport.close()
        raise
    return engine


def engine_config(profile: EngineProfile) -> EngineConfig:
    return EngineConfig(
        binary_path=profile.binary_path,
        threads=profile.threads,
        hash_size=profile.hash_size,
        name=profile.name,
        node_scale=profile.node_scale,
        score_scale=profile.score_scale,
        protocol=profile.protocol,
        options=profile.options,
        limits=profile.limits,
        ladder=profile.ladder,
    )


def declared_variants(engine: chess.engine.UciProtocol) -> list[str] | None:
    """The engine's ``UCI_Variant`` values; ``None`` when it has no such option."""
    option = engine.options.get("UCI_Variant")
    return None if option is None else list(option.var or [])


def undeclared_reason(engine: chess.engine.UciProtocol, variant: str) -> str | None:
    name = engine.id.get("name", "the engine")
    declared = declared_variants(engine)
    if declared is None:
        return f"{name} declares no UCI_Variant option"
    if variant not in declared:
        return f"{name} does not list {variant} in UCI_Variant"
    return None


# ``info`` keys that can follow a ``pv``; none of them is a move.
INFO_KEYS = frozenset(
    {
        "depth",
        "seldepth",
        "time",
        "nodes",
        "pv",
        "multipv",
        "score",
        "cp",
        "mate",
        "lowerbound",
        "upperbound",
        "currmove",
        "currmovenumber",
        "hashfull",
        "nps",
        "tbhits",
        "sbhits",
        "cpuload",
        "refutation",
        "currline",
        "wdl",
        "string",
    }
)


def final_raw_pvs(received: Iterable[str]) -> dict[int, list[str]]:
    """The last ``pv`` the engine sent for each MultiPV index, as raw tokens."""
    pvs: dict[int, list[str]] = {}
    for line in received:
        tokens = line.split()
        if len(tokens) < 2 or tokens[0] != "info" or tokens[1] == "string" or "pv" not in tokens:
            continue
        index = 1
        if "multipv" in tokens:
            index = int(tokens[tokens.index("multipv") + 1])
        start = tokens.index("pv") + 1
        end = next((i for i in range(start, len(tokens)) if tokens[i] in INFO_KEYS), len(tokens))
        pvs[index] = tokens[start:end]
    return pvs


def first_illegal_ply(board: chess.Board, moves: Sequence[str]) -> int | None:
    """0-based ply of the first move the variant rules reject; ``None`` if all are legal."""
    position = board.copy(stack=False)
    for ply, token in enumerate(moves):
        try:
            move = chess.Move.from_uci(token)
        except ValueError:
            return ply
        if move not in position.legal_moves:
            return ply
        position.push(move)
    return None


async def probe_position(
    engine: RecordingUci, position: ProbePosition, nodes: int, multipv: int
) -> dict[str, Any]:
    board = position.board()
    record: dict[str, Any] = {
        "variant": position.variant,
        "variant_name": type(board).aliases[0],
        "board": type(board).__name__,
        "position": position.label,
        "fen": position.fen,
        "note": position.note,
        "legal_moves": board.legal_moves.count(),
        "lines": [],
    }
    reason = undeclared_reason(engine, position.variant)
    if reason is not None:
        return {**record, "status": "not declared", "reason": reason}
    engine.received.clear()
    started = time.perf_counter()
    try:
        infos = await engine.analyse(
            board, chess.engine.Limit(nodes=nodes), multipv=multipv, game=object()
        )
    except chess.engine.EngineError as error:
        return {
            **record,
            "status": "error",
            "reason": f"{type(error).__name__}: {error}",
            "time_s": round(time.perf_counter() - started, 3),
        }
    elapsed = time.perf_counter() - started
    raw = final_raw_pvs(engine.received)
    parsed = {info.get("multipv", index): info for index, info in enumerate(infos, start=1)}
    lines = []
    for index in sorted(set(raw) | set(parsed)):
        info = parsed.get(index)
        line = line_from_info(info, board.turn) if info is not None else {}
        parsed_pv = line.pop("pv", [])
        moves = raw.get(index, [])
        illegal = first_illegal_ply(board, moves)
        line.update(
            {
                "multipv": index,
                "pv": moves,
                "pv_length": len(moves),
                "legal": illegal is None,
                "first_illegal_ply": illegal,
                "parsed_pv_matches": parsed_pv == moves,
            }
        )
        if info is not None and "time" in info:
            line["engine_time_s"] = info["time"]
        lines.append(line)
    return {
        **record,
        "status": "analysed",
        "time_s": round(elapsed, 3),
        "lines": lines,
        "lines_returned": len(lines),
        "pv_lengths": [line["pv_length"] for line in lines],
        "all_pv_legal": all(line["legal"] for line in lines),
    }


def manifest_path(results: Path) -> Path:
    return results.with_name(results.stem + ".manifest.json")


def run_manifest(
    config: ProbeConfig,
    profile: EngineProfile,
    engine: chess.engine.UciProtocol,
    positions: Sequence[ProbePosition],
) -> dict[str, Any]:
    settings = {
        "nodes": config.nodes,
        "multipv": config.multipv,
        "threads": THREADS,
        "hash_mb": profile.hash_size,
        "positions": [{"variant": p.variant, "label": p.label, "fen": p.fen} for p in positions],
    }
    config_file = {"path": shown(config.path), "sha256": file_sha256(config.path)}
    profile_file = {"path": shown(config.profile), "sha256": file_sha256(config.profile)}
    options = {
        name: engine.config.get(name, option.default)
        for name, option in sorted(engine.options.items())
        if not option.is_managed() and option.type not in ("button", "reset", "save")
    }
    return {
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": code_provenance(config.results_dir),
        "config": config_file,
        "profile": profile_file,
        "settings": settings,
        "config_digest": _digest(
            {"config": config_file, "profile": profile_file, "settings": settings}
        ),
        "engine": {
            **engine_identity(engine, profile.binary_path),
            "options": options,
            "declared_variants": declared_variants(engine),
        },
        "python_chess": chess.__version__,
        "python": platform.python_version(),
        "cpu_affinity": cpu_affinity(),
        "env": {POOL_ENV: os.environ.get(POOL_ENV)},
    }


def write_manifest(results: Path, manifest: dict[str, Any]) -> str:
    path = manifest_path(results)
    if path.exists() or results.exists():
        raise ValueError(f"{results} or its manifest already exists; a probe run writes new files")
    run_id = _digest(manifest)
    path.write_text(
        json.dumps({"run_id": run_id, "manifest": manifest}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run_id


def append(results: Path, record: dict[str, Any]) -> None:
    with results.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


async def run_probe(
    config: ProbeConfig, results: Path, positions: Sequence[ProbePosition] = POSITIONS
) -> str:
    """Probe every position into ``results``; returns the run id."""
    validate_positions(positions)
    profile = load_engine_profile(config.profile)
    if profile.protocol != "uci":
        raise ValueError(f"{config.profile}: the probe needs a UCI engine (MultiPV)")
    if profile.threads != THREADS:
        raise ValueError(f"{config.profile}: the probe runs one thread, not {profile.threads}")
    engine = await start_engine(profile.binary_path)
    try:
        await engine.configure(startup_options(engine, engine_config(profile)))
        run_id = write_manifest(results, run_manifest(config, profile, engine, positions))
        for position in positions:
            record = await probe_position(engine, position, config.nodes, config.multipv)
            append(results, {"run_id": run_id, **record})
            print(
                f"{position.variant}/{position.label}: {record['status']}, "
                f"{len(record['lines'])} lines, {record.get('time_s', '-')} s",
                flush=True,
            )
    finally:
        await engine.quit()
    return run_id


def _score(line: dict[str, Any]) -> str:
    if "score_mate" in line:
        return f"mate {line['score_mate']}"
    if "score_cp" in line:
        return f"{line['score_cp']} cp"
    return "none"


def summarise(results: Path) -> str:
    """Markdown summary from the manifest and records only; legality is re-checked."""
    recorded = json.loads(manifest_path(results).read_text(encoding="utf-8"))
    run_id, manifest = recorded["run_id"], recorded["manifest"]
    records = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if record["run_id"] != run_id:
            raise ValueError(f"{results} holds a record of run {record['run_id']}, not {run_id}")
    engine, settings, code = manifest["engine"], manifest["settings"], manifest["code"]
    declared = engine["declared_variants"]
    out = [
        "# Variant engine probe",
        "",
        f"- Run: `{run_id}` (`{manifest_path(results).name}`), started {manifest['started_at']}",
        f"- Code: commit `{code['commit']}`, dirty {str(code['dirty']).lower()} "
        "(whole tree, results directory excluded)",
        f"- Engine: {engine['engine_name']}, executable `{engine['engine_version']}`, "
        f"network {engine['network'] or 'none (built in or classical)'}",
        f"- Profile: `{manifest['profile']['path']}` (sha256 `{manifest['profile']['sha256']}`)",
        f"- Config: `{manifest['config']['path']}` (sha256 `{manifest['config']['sha256']}`), "
        f"config digest `{manifest['config_digest']}`",
        f"- Search: {settings['nodes']:,} nodes, MultiPV {settings['multipv']}, "
        f"{settings['threads']} thread, {settings['hash_mb']} MB hash, fresh game per position",
        f"- python-chess {manifest['python_chess']}, Python {manifest['python']}, "
        f"CPUs {manifest['cpu_affinity']}",
        "- Declared UCI_Variant values for the probed variants: "
        + (
            "no UCI_Variant option"
            if declared is None
            else ", ".join(
                f"{variant} {'yes' if variant in declared else 'no'}" for variant in BOARDS
            )
        ),
        "",
        "PV lengths are plies per line, best line first. Legality is every move of the raw "
        "`pv` the engine sent, replayed on the python-chess variant board. The score is the "
        "best line's, from the side to move. Time is wall-clock for the whole search.",
        "",
        "| Variant (board -> UCI_Variant) | Position | Legal moves | Status | Lines | "
        "PV lengths | All PV moves legal | Best score | Time (s) |",
        "| --- | --- | ---: | --- | ---: | --- | --- | --- | ---: |",
    ]
    for record in records:
        board = BOARDS[record["variant"]](record["fen"])
        lines = record["lines"]
        legal = [first_illegal_ply(board, line["pv"]) is None for line in lines]
        variant = f"{record['board']} -> {record['variant']}"
        if record["status"] != "analysed":
            out.append(
                f"| {variant} | {record['position']} | {record['legal_moves']} | "
                f"{record['status']}: {record['reason']} | 0 | - | - | - | - |"
            )
            continue
        lengths = ", ".join(str(len(line["pv"])) for line in lines)
        out.append(
            f"| {variant} | {record['position']} | {record['legal_moves']} | analysed | "
            f"{len(lines)} | {lengths} | {'yes' if lines and all(legal) else 'no'} | "
            f"{_score(lines[0]) if lines else 'none'} | {record['time_s']:.2f} |"
        )
    mismatched = [
        f"{record['variant']}/{record['position']} line {line['multipv']}"
        for record in records
        for line in record["lines"]
        if not line["parsed_pv_matches"]
    ]
    out += [
        "",
        "Lines where python-chess's parsed PV differs from the raw PV: "
        + (", ".join(mismatched) if mismatched else "none"),
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path, nargs="?")
    parser.add_argument(
        "--summarise", type=Path, help="Rewrite the summary of a results JSONL; no engine starts"
    )
    args = parser.parse_args(argv)
    if args.summarise is not None:
        results = args.summarise
    else:
        if args.config is None:
            parser.error("config is required unless --summarise is given")
        config = load_config(args.config)
        config.results_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        results = config.results_dir / f"variants_probe_{stamp}.jsonl"
        asyncio.run(run_probe(config, results))
    summary = results.with_suffix(".md")
    summary.write_text(summarise(results), encoding="utf-8")
    print(f"Results: {shown(results)}; summary: {shown(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
