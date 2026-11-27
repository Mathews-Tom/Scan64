"""Measured playing strength of each bot-ladder level under an Elo ladder profile.

Usage:
    uv run python -m benchmarks.engines.ladder_strength benchmarks/engines/ladder_strength.toml
    uv run python -m benchmarks.engines.ladder_strength CONFIG --resume RESULTS.jsonl
    uv run python -m benchmarks.engines.ladder_strength CONFIG --level 3 --level 4

The ladder side plays through production code: ``PooledStockfishAdapter``
built from the ladder profile (``[ladder] strength = "elo"``), one
``play_bot_move`` per move, a fresh game token per game. The reference side is
an engine with its own calibrated ``UCI_LimitStrength``/``UCI_Elo`` (Stockfish
18 declares 1320-3190), set to the level's anchor Elo. Each opening is played
once with each colour per round.

One record per game is appended to ``ladder_strength_<UTC stamp>.jsonl`` when
it ends; ``--resume`` skips games already recorded. A markdown table is written
next to it. A level's measured strength is ``anchor + 400 * log10(s / (1 - s))``
for its score ``s`` over decided-or-drawn games, with a 95% interval from the
score's standard error. A score of 0 or 1 has no finite estimate and is
reported as outside the measurable range of that anchor. Games that reach
``max_plies`` are recorded as ``ply_limit`` and left out of the score.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
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

from scan64.chess.opponents.bot_policy import bot_policy_version
from scan64.chess.opponents.bots import BOT_LEVELS
from scan64.chess.opponents.protocols import OpponentContext
from scan64.providers.engine.config import EngineConfig, EngineProfile, load_engine_profile
from scan64.providers.engine.uci import (
    ELO_LADDER_OPTIONS,
    Engine,
    open_engine,
    require_options,
    within_declared_range,
)
from scan64.providers.stockfish.pool import PooledStockfishAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("ladder_strength")
Z_95 = 1.96


@dataclass(frozen=True)
class StrengthConfig:
    results_dir: Path
    ladder_profile: Path
    reference_profile: Path
    reference_movetime_ms: int
    anchors: tuple[int, ...]
    openings: tuple[tuple[str, ...], ...]
    rounds: int
    max_plies: int


def _profile_path(raw: str) -> Path:
    """Absolute (``~`` expands) or relative to the repository root."""
    path = Path(os.path.expanduser(raw))
    return path if path.is_absolute() else REPO_ROOT / path


def shown(path: Path) -> str:
    """Repository-relative when inside the repository, so reports carry no home path."""
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def _positive(run: dict[str, Any], key: str) -> int:
    value = run[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"run.{key} must be a positive integer")
    return value


def load_config(path: Path) -> StrengthConfig:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    run = raw["run"]
    anchors = tuple(run["anchors"])
    if len(anchors) != len(BOT_LEVELS) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in anchors
    ):
        raise ValueError(f"run.anchors must list {len(BOT_LEVELS)} integers, one per level")
    openings: list[tuple[str, ...]] = []
    for index, line in enumerate(run["openings"]):
        moves = tuple(line.split())
        board = chess.Board()
        for uci in moves:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                raise ValueError(f"run.openings[{index}] move {uci} is illegal after {board.fen()}")
            board.push(move)
        openings.append(moves)
    if not openings:
        raise ValueError("run.openings must list at least one opening")
    return StrengthConfig(
        results_dir=_profile_path(run["results_dir"]),
        ladder_profile=_profile_path(run["ladder_profile"]),
        reference_profile=_profile_path(run["reference_profile"]),
        reference_movetime_ms=_positive(run, "reference_movetime_ms"),
        anchors=anchors,
        openings=tuple(openings),
        rounds=_positive(run, "rounds"),
        max_plies=_positive(run, "max_plies"),
    )


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


GameKey = tuple[int, int, str, int]


def done_keys(results: Path) -> set[GameKey]:
    if not results.exists():
        return set()
    return {
        (record["level"], record["opening"], record["ladder_color"], record["round"])
        for record in map(json.loads, results.read_text(encoding="utf-8").splitlines())
    }


def game_seed(key: GameKey) -> int:
    """Stable across runs, unlike ``hash`` of a tuple holding a string."""
    digest = hashlib.sha256(json.dumps(key).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def append(results: Path, record: dict[str, Any]) -> None:
    with results.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


async def configure_reference(reference: Engine, anchor: int) -> None:
    require_options(reference, ELO_LADDER_OPTIONS, "The reference engine")
    if within_declared_range(reference, "UCI_Elo", anchor) != anchor:
        declared = reference.options["UCI_Elo"]
        raise ValueError(
            f"Anchor UCI_Elo {anchor} is outside the {declared.min}-{declared.max} "
            f"that {reference.id.get('name', 'the reference engine')} declares"
        )
    await reference.configure({"Threads": 1, "UCI_LimitStrength": True, "UCI_Elo": anchor})


async def play_game(
    ladder: PooledStockfishAdapter,
    reference: Engine,
    config: StrengthConfig,
    level: int,
    opening: tuple[str, ...],
    ladder_color: chess.Color,
    seed: int,
) -> dict[str, Any]:
    await ladder.reset()
    reference_game = object()
    board = chess.Board()
    for uci in opening:
        board.push(chess.Move.from_uci(uci))
    limit = chess.engine.Limit(time=config.reference_movetime_ms / 1000)
    while not board.is_game_over(claim_draw=True) and board.ply() < config.max_plies:
        if board.turn == ladder_color:
            context = OpponentContext(0, bot_level=level, seed=seed, ply=board.ply())
            result = await ladder.play_bot_move(board.fen(), context)
        else:
            result = await reference.play(board, limit, game=reference_game)
        if result.move is None or result.move not in board.legal_moves:
            raise chess.engine.EngineError(
                f"{'ladder' if board.turn == ladder_color else 'reference'} engine returned "
                f"{result.move} in {board.fen()}"
            )
        board.push(result.move)
    outcome = board.outcome(claim_draw=True)
    return {
        "result": outcome.result() if outcome is not None else "*",
        "termination": outcome.termination.name.lower() if outcome is not None else "ply_limit",
        "plies": board.ply(),
        "moves": [move.uci() for move in board.move_stack],
    }


def ladder_score(record: dict[str, Any]) -> float | None:
    """1, 0.5 or 0 from the ladder side; ``None`` for a game cut at ``max_plies``."""
    if record["result"] == "*":
        return None
    if record["result"] == "1/2-1/2":
        return 0.5
    return float(record["result"] == ("1-0" if record["ladder_color"] == "white" else "0-1"))


def elo_from(score: float, anchor: int) -> float:
    return anchor + 400 * math.log10(score / (1 - score))


def summarise(results: Path, config: StrengthConfig, header: dict[str, str]) -> str:
    records = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    lines = [
        "# Bot ladder strength",
        "",
        *(f"- {key}: {value}" for key, value in header.items()),
        f"- Reference move time: {config.reference_movetime_ms} ms; "
        f"{len(config.openings)} openings x 2 colours x {config.rounds} rounds per level; "
        f"max {config.max_plies} plies",
        "",
        "| Level | UCI_Elo sent | Anchor | Games | W-D-L | Ply limit | Score "
        "| Measured Elo (95%) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for level in sorted({record["level"] for record in records}):
        rows = [record for record in records if record["level"] == level]
        scores = [score for score in map(ladder_score, rows) if score is not None]
        anchor = rows[0]["anchor"]
        wins, draws = scores.count(1.0), scores.count(0.5)
        losses = len(scores) - wins - draws
        measured = "no scored games"
        if scores:
            mean = statistics.fmean(scores)
            if mean in (0.0, 1.0):
                side = "below" if mean == 0.0 else "above"
                measured = f"{side} the measurable range of anchor {anchor}"
            else:
                error = statistics.stdev(scores) / math.sqrt(len(scores)) if len(scores) > 1 else 0
                low = max(mean - Z_95 * error, 1e-9)
                high = min(mean + Z_95 * error, 1 - 1e-9)
                measured = (
                    f"{elo_from(mean, anchor):.0f} "
                    f"({elo_from(low, anchor):.0f} to {elo_from(high, anchor):.0f})"
                )
        lines.append(
            f"| {level} | {rows[0]['sent_elo']} | {anchor} | {len(rows)} | "
            f"{wins}-{draws}-{losses} | {len(rows) - len(scores)} | "
            f"{statistics.fmean(scores) if scores else 0:.3f} | {measured} |"
        )
    return "\n".join(lines) + "\n"


async def run_levels(
    config: StrengthConfig, levels: tuple[int, ...], results: Path
) -> dict[str, str]:
    ladder_profile = load_engine_profile(config.ladder_profile)
    if ladder_profile.ladder.strength != "elo":
        raise ValueError(f'{config.ladder_profile} has no [ladder] strength = "elo"')
    ladder_config = engine_config(ladder_profile)
    reference_config = engine_config(load_engine_profile(config.reference_profile))
    done = done_keys(results)
    ladder = PooledStockfishAdapter(ladder_config)
    reference = await open_engine(reference_config)
    try:
        await ladder.ensure_started()
        header = {
            "Ladder profile": shown(config.ladder_profile),
            "Ladder policy": bot_policy_version(ladder_config),
            "Reference": reference.id.get("name", reference_config.name),
        }
        for level in levels:
            anchor = config.anchors[level - 1]
            await configure_reference(reference, anchor)
            for round_index in range(config.rounds):
                for opening_index, opening in enumerate(config.openings):
                    for color in (chess.WHITE, chess.BLACK):
                        name = "white" if color == chess.WHITE else "black"
                        key = (level, opening_index, name, round_index)
                        if key in done:
                            continue
                        seed = game_seed(key)
                        game = await play_game(
                            ladder, reference, config, level, opening, color, seed
                        )
                        record = {
                            "level": level,
                            "sent_elo": ladder_profile.ladder.elo_for(level),
                            "anchor": anchor,
                            "opening": opening_index,
                            "ladder_color": name,
                            "round": round_index,
                            **game,
                        }
                        append(results, record)
                        LOG.info(
                            "level %d opening %d %s round %d: %s (%s, %d plies)",
                            level,
                            opening_index,
                            name,
                            round_index,
                            game["result"],
                            game["termination"],
                            game["plies"],
                        )
        return header
    finally:
        await ladder.quit()
        await reference.quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--resume", type=Path, help="Existing results JSONL to continue")
    parser.add_argument(
        "--level", type=int, action="append", help="Run only this level (repeatable)"
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    levels = tuple(args.level or (bot.level for bot in BOT_LEVELS))
    unknown = sorted(set(levels) - {bot.level for bot in BOT_LEVELS})
    if unknown:
        raise SystemExit(f"--level names no bot level: {unknown}")
    config.results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    results = args.resume or config.results_dir / f"ladder_strength_{stamp}.jsonl"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.results_dir / f"ladder_strength_{stamp}.log"),
        ],
    )
    header = asyncio.run(run_levels(config, levels, results))
    summary = results.with_suffix(".md")
    summary.write_text(summarise(results, config, header), encoding="utf-8")
    LOG.info("Results: %s; summary: %s", results, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
