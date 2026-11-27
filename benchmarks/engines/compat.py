"""Engine compatibility set: which Scan64 features work with each engine.

Usage:
    uv run python -m benchmarks.engines.compat benchmarks/engines/compat.toml
    uv run python -m benchmarks.engines.compat CONFIG --resume RESULTS.jsonl
    uv run python -m benchmarks.engines.compat CONFIG --only "Fairy-Max"

Each engine is selected exactly as a user selects it: ``SCAN64_ENGINE_CONFIG``
points at its engine profile. Every feature then runs through its production
entry point and budget (review fast pass, focused pass, play-assistance
hints, lesson-source verification, Analysis Board lines, the bot ladder and
the legacy Skill Level opponent). One record per (engine, feature) is appended
to ``compat_<UTC stamp>.jsonl`` as soon as it finishes; ``--resume`` skips
records already present. A markdown feature table is written next to it.

A feature ends in one of four states:
- ``works``: the production call returned a result Scan64 accepts.
- ``unavailable``: ``EngineCapabilityError``; the API answers 503
  ``engine_capability_unavailable`` with this exact reason.
- ``blocked``: a feature it depends on (the fast pass) did not work.
- ``error``: any other exception, with its type and message. This is a bug:
  the acceptance criterion is a capability message, never a crash.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chess
import chess.engine

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import (
    CandidatePosition,
    FastPassConfig,
    FastPassOrchestrator,
    FocusedPassConfig,
    FocusedPassOrchestrator,
)
from scan64.chess.analysis.review import REVIEW_NODES
from scan64.chess.opponents.protocols import OpponentContext
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.chess.positions.models import Position
from scan64.explanations.board_lines import scored_legal_lines
from scan64.learning.exercises.sound_moves import analyze_lesson_source
from scan64.learning.play_assistance import grade_play_moves
from scan64.providers.engine.config import ENGINE_CONFIG_ENV, EngineConfig, load_engine_profile
from scan64.providers.engine.uci import EngineCapabilityError
from scan64.providers.stockfish.identity import executable_signature
from scan64.providers.stockfish.pool import EnginePoolManager

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("engine_compat")

FEATURES = (
    "game_analysis",
    "focused_pass",
    "hints",
    "lesson_verification",
    "analysis_board",
    "bot_ladder",
    "skill_level_opponent",
)
# The values the analysis job passes (scan64.chess.analysis.jobs).
REVIEW_SWING_THRESHOLD_CP = 150
FOCUSED_NODES = 1_000_000
FOCUSED_MULTIPV = 4
# Play assistance refuses a pool with fewer interactive engines (hints keep
# one free for opponent moves).
INTERACTIVE_ENGINES = 2


@dataclass(frozen=True)
class EngineEntry:
    name: str
    profile: Path
    licence: str
    source: str
    stresses: str


@dataclass(frozen=True)
class CompatConfig:
    results_dir: Path
    game: tuple[str, ...]
    focused_plies: tuple[int, ...]
    position: str
    lesson_position: str
    bot_level: int
    skill_level: int
    engines: tuple[EngineEntry, ...]


def _profile_path(raw: str) -> Path:
    """Absolute (``~`` expands) or relative to the repository root."""
    path = Path(os.path.expanduser(raw))
    return path if path.is_absolute() else REPO_ROOT / path


def load_config(path: Path) -> CompatConfig:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    run = raw["run"]
    game = tuple(run["game"])
    board = chess.Board()
    for uci in game:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"run.game move {uci} is illegal after {board.fen()}")
        board.push(move)
    focused_plies = tuple(int(ply) for ply in run["focused_plies"])
    if not focused_plies or any(not 1 <= ply <= len(game) for ply in focused_plies):
        raise ValueError(f"run.focused_plies must be plies in 1..{len(game)}")
    for key in ("position", "lesson_position"):
        if not chess.Board(run[key]).is_valid():
            raise ValueError(f"run.{key} is not a valid FEN: {run[key]}")
    engines = tuple(
        EngineEntry(
            name=entry["name"],
            profile=_profile_path(entry["profile"]),
            licence=entry["licence"],
            source=entry["source"],
            stresses=entry["stresses"],
        )
        for entry in raw["engines"]
    )
    return CompatConfig(
        results_dir=REPO_ROOT / run["results_dir"],
        game=game,
        focused_plies=focused_plies,
        position=run["position"],
        lesson_position=run["lesson_position"],
        bot_level=int(run["bot_level"]),
        skill_level=int(run["skill_level"]),
        engines=engines,
    )


def done_keys(results: Path) -> set[tuple[str, str]]:
    if not results.exists():
        return set()
    return {
        (record["engine"], record["feature"])
        for record in map(json.loads, results.read_text(encoding="utf-8").splitlines())
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def code_provenance() -> dict[str, Any]:
    """The Scan64 code under test; ``dirty`` covers ``src`` and ``benchmarks``."""
    return {
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain", "--", "src", "benchmarks")),
    }


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def append(results: Path, record: dict[str, Any]) -> None:
    with results.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def _line_counts(analyses: list[EngineAnalysis]) -> list[int]:
    return [len(analysis.raw_result) for analysis in analyses]


def _san_moves(game: tuple[str, ...]) -> list[str]:
    board = chess.Board()
    sans = []
    for uci in game:
        move = chess.Move.from_uci(uci)
        sans.append(board.san(move))
        board.push(move)
    return sans


class EngineRun:
    """Every feature for one engine profile, sharing one pool manager."""

    def __init__(self, config: CompatConfig, engine_config: EngineConfig) -> None:
        self.config = config
        self.engine_config = engine_config
        self.manager = EnginePoolManager(
            engine_config, interactive_concurrency=INTERACTIVE_ENGINES, batch_concurrency=1
        )
        self.fast_pass_evaluations: list[EngineAnalysis] | None = None

    async def game_analysis(self) -> dict[str, Any]:
        orchestrator = FastPassOrchestrator(
            self.manager.batch_adapter,
            FastPassConfig(nodes=REVIEW_NODES, swing_threshold_cp=REVIEW_SWING_THRESHOLD_CP),
        )
        result = await orchestrator.run_fast_pass(_san_moves(self.config.game))
        self.fast_pass_evaluations = result.evaluations
        return {
            "positions": len(result.evaluations),
            "candidates": len(result.candidates),
            "lines": _line_counts(result.evaluations),
        }

    async def focused_pass(self) -> dict[str, Any]:
        evaluations = self.fast_pass_evaluations
        assert evaluations is not None
        board = chess.Board()
        fens = [board.fen()]
        for uci in self.config.game:
            board.push_uci(uci)
            fens.append(board.fen())
        candidates = [
            CandidatePosition(
                fen=fens[ply],
                move_index=ply - 1,
                before_analysis=evaluations[ply - 1],
                after_analysis=evaluations[ply],
                swing_cp=0,
            )
            for ply in self.config.focused_plies
        ]
        orchestrator = FocusedPassOrchestrator(
            self.manager.batch_adapter,
            FocusedPassConfig(nodes=FOCUSED_NODES, multipv=FOCUSED_MULTIPV),
        )
        analyses = await orchestrator.run_focused_pass(candidates)
        return {"lines": _line_counts(analyses), "multipv_requested": FOCUSED_MULTIPV}

    async def hints(self) -> dict[str, Any]:
        board = chess.Board(self.config.position)
        move = sorted(board.legal_moves, key=chess.Move.uci)[0]
        [grade] = await grade_play_moves(board, [move], self.manager)
        return {"move": grade.move, "band": str(grade.band)}

    async def lesson_verification(self) -> dict[str, Any]:
        analysis = await analyze_lesson_source(
            self.manager.batch_adapter, self.config.lesson_position
        )
        return {"lines": len(analysis.raw_result), "multipv": analysis.config["multipv"]}

    async def analysis_board(self) -> dict[str, Any]:
        board = chess.Board(self.config.position)
        lines = scored_legal_lines(board, await self.manager.analyze_hint(board))
        if not lines:
            raise ValueError("The engine returned no scored legal line")
        return {"lines": len(lines), "pv_lengths": [len(line.moves) for line in lines]}

    async def _opponent_move(self, context: OpponentContext) -> dict[str, Any]:
        position = Position(
            fen=self.config.position,
            side_to_move="w" if chess.Board(self.config.position).turn else "b",
            canonical_id="compat",
        )
        decision = await StockfishOpponentProvider(self.engine_config, self.manager).choose_move(
            position, context
        )
        return {"move": decision.uci_move}

    async def bot_ladder(self) -> dict[str, Any]:
        return await self._opponent_move(
            OpponentContext(0, bot_level=self.config.bot_level, seed=1, ply=0)
        )

    async def skill_level_opponent(self) -> dict[str, Any]:
        return await self._opponent_move(OpponentContext(self.config.skill_level))

    def feature(self, name: str) -> Callable[[], Awaitable[dict[str, Any]]]:
        return {
            "game_analysis": self.game_analysis,
            "focused_pass": self.focused_pass,
            "hints": self.hints,
            "lesson_verification": self.lesson_verification,
            "analysis_board": self.analysis_board,
            "bot_ladder": self.bot_ladder,
            "skill_level_opponent": self.skill_level_opponent,
        }[name]


async def run_feature(run: EngineRun, name: str) -> dict[str, Any]:
    if name == "focused_pass" and run.fast_pass_evaluations is None:
        return {"status": "blocked", "reason": "game_analysis did not work"}
    started = time.monotonic()
    try:
        detail = await run.feature(name)()
    except EngineCapabilityError as error:
        return {"status": "unavailable", "reason": str(error)}
    except Exception as error:  # noqa: BLE001 - every other failure is recorded as a bug
        LOG.exception("%s: unexpected failure", name)
        return {"status": "error", "reason": f"{type(error).__name__}: {error}"}
    return {"status": "works", "seconds": round(time.monotonic() - started, 3), **detail}


async def run_engine(
    entry: EngineEntry, config: CompatConfig, results: Path, done: set[tuple[str, str]]
) -> None:
    pending = [name for name in FEATURES if (entry.name, name) not in done]
    if not pending:
        return
    if not entry.profile.is_file():
        LOG.warning("%s: profile not found at %s", entry.name, entry.profile)
        append(
            results,
            {
                "engine": entry.name,
                "feature": "*",
                "status": "not installed",
                "reason": f"profile not found: {entry.profile}",
            },
        )
        return
    profile = load_engine_profile(entry.profile)
    try:
        executable = executable_signature(profile.binary_path)[0]
    except FileNotFoundError:
        LOG.warning("%s: engine binary not found at %s", entry.name, profile.binary_path)
        append(
            results,
            {
                "engine": entry.name,
                "feature": "*",
                "status": "not installed",
                "reason": f"engine binary not found: {profile.binary_path}",
            },
        )
        return
    os.environ[ENGINE_CONFIG_ENV] = str(entry.profile)
    engine_config = EngineConfig()
    run = EngineRun(config, engine_config)
    facts = {
        "profile": profile.name,
        "protocol": profile.protocol,
        "node_scale": profile.node_scale,
        **code_provenance(),
        "profile_sha256": file_sha256(entry.profile),
        "binary_sha256": file_sha256(executable),
    }
    try:
        # game_analysis always runs first: the focused pass needs its evaluations.
        for name in FEATURES:
            if (entry.name, name) in done and name != "game_analysis":
                continue
            outcome = await run_feature(run, name)
            if (entry.name, name) in done:
                continue
            record = {"engine": entry.name, "feature": name, **facts, **outcome}
            append(results, record)
            LOG.info("%s %s: %s %s", entry.name, name, outcome["status"], outcome.get("reason", ""))
    finally:
        await run.manager.close()


def summarise(results: Path, config: CompatConfig) -> str:
    records = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    header = "| Engine | Licence | Stresses | " + " | ".join(FEATURES) + " |"
    rows = [header, "|" + "---|" * (3 + len(FEATURES))]
    reasons: list[str] = []
    for entry in config.engines:
        mine = {record["feature"]: record for record in records if record["engine"] == entry.name}
        if "*" in mine:
            cells = ["not installed"] * len(FEATURES)
        else:
            cells = []
            for name in FEATURES:
                record = mine.get(name)
                if record is None:
                    cells.append("—")
                    continue
                cells.append(record["status"])
                if record["status"] != "works":
                    reasons.append(
                        f"- {entry.name}, `{name}`: {record['status']}: {record['reason']}"
                    )
        rows.append(
            f"| [{entry.name}]({entry.source}) | {entry.licence} | {entry.stresses} | "
            + " | ".join(cells)
            + " |"
        )
    text = "\n".join(rows) + "\n"
    if reasons:
        text += "\nExact reasons:\n\n" + "\n".join(reasons) + "\n"
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--resume", type=Path, help="Existing results JSONL to continue")
    parser.add_argument("--only", action="append", help="Run only this engine name (repeatable)")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    unknown = set(args.only or ()) - {entry.name for entry in config.engines}
    if unknown:
        raise SystemExit(f"--only names no engine in {args.config}: {sorted(unknown)}")
    config.results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    results = args.resume or config.results_dir / f"compat_{stamp}.jsonl"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.results_dir / f"compat_{stamp}.log"),
        ],
    )
    done = done_keys(results)

    async def run_all() -> None:
        for entry in config.engines:
            if args.only and entry.name not in args.only:
                continue
            await run_engine(entry, config, results, done)

    asyncio.run(run_all())
    summary = results.with_suffix(".md")
    summary.write_text(summarise(results, config), encoding="utf-8")
    LOG.info("Results: %s; summary: %s", results, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
