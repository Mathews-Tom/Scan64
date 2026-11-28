"""Evaluate the fine-tune against its base, the default model and the phrase bank (#465).

Game moves go through the app's voice pipeline (#454) and its role facts, on the legal
#462 positions and on every commented move of the #464 held-out games. The Analysis Board
path is ``benchmarks.narrator.board_path`` on its stored lines. All arms then share one
blind set, and one judge reads it.

    python -m benchmarks.narrator.finetune.evaluate positions --data-dir D --engine stockfish
    python -m benchmarks.narrator.finetune.evaluate run --arm tuned --model NAME --out R.jsonl
    python -m benchmarks.narrator.finetune.evaluate run --arm phrase-bank --out R.jsonl
    python -m benchmarks.narrator.finetune.evaluate blind R.jsonl ... --board arm=B.jsonl ... \
        --out judge/ --key-out key.json
    python -m benchmarks.narrator.finetune.evaluate summarise R.jsonl ... --board ... \
        --key key.json --judgements judge/items-judgements.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import chess
import chess.engine
import httpx

from benchmarks.narrator import board_path
from benchmarks.narrator.finetune.data import HERE, config, heldout_positions, sha256
from benchmarks.narrator.voice import blind as voice_blind
from scan64.explanations.phrase_bank import phrase_facts
from scan64.explanations.role_facts import RoleFact, numbered_san, role_facts, role_label
from scan64.explanations.voice_pipeline import voice_facts
from scan64.providers.llm.adapters import OllamaExplanationAdapter

Row = dict[str, Any]
CONTEXT_TOKENS = 4096
DEFAULT_URL = "http://127.0.0.1:11434"
POSITIONS = HERE / "results" / "positions.json"
BOARD_LINES = HERE.parent / "results" / "board-path" / "lines.json"
# Played and best move of the #462 game-move positions. H2 is illegal (#500).
BENCHMARK_MOVES = {
    "H1-missed-mate": ("d2d3", "h5f7"),
    "H3-stalemate": ("f1f7", "f1f8"),
    "H4-pin": ("g1g2", "f2f4"),
    "H5-allowed-fork": ("d1c1", "d1d4"),
}


def build_positions(data_dir: Path, engine_path: str) -> list[Row]:
    """The test positions with their engine lines, so every arm words the same facts."""
    out: list[Row] = []
    nodes = config()["evaluate"]["nodes"]
    for test, (played, best) in BENCHMARK_MOVES.items():
        board = chess.Board(board_path.POSITIONS[test])
        after = board.copy()
        after.push_uci(played)
        with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
            engine.configure({"Threads": 1, "Hash": 64})
            limit = chess.engine.Limit(nodes=nodes)
            root = [chess.Move.from_uci(best)]
            best_info = engine.analyse(board, limit, root_moves=root)
            line = {} if after.is_game_over() else engine.analyse(after, limit)
        out.append(
            {
                "set": "benchmark",
                "test": test,
                "fen": board.fen(),
                "played": played,
                "best_line": [m.uci() for m in best_info["pv"]],
                "played_line": [m.uci() for m in line.get("pv", [])],
            }
        )
    for entry in heldout_positions(data_dir):
        record = entry["record"]
        out.append(
            {
                "set": "heldout",
                "test": entry["test"],
                "fen": entry["fen"],
                "played": entry["played"],
                "best_line": record["best_line"],
                "played_line": record["played_line"],
            }
        )
    return out


def facts_for(position: Row) -> tuple[RoleFact, ...]:
    return role_facts(
        position["fen"],
        position["played"],
        position["best_line"][0],
        best_line=position["best_line"],
        played_line=position["played_line"],
    )


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def _meta(out: Path) -> Row:
    dirty = [
        line for line in _git("status", "--porcelain").splitlines() if not line.endswith(out.name)
    ]
    return {
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(dirty),
        "positions_sha256": sha256(POSITIONS),
        "config_sha256": sha256(HERE / "finetune.toml"),
        "board_lines_sha256": sha256(BOARD_LINES),
    }


async def run(arm: str, model: str | None, url: str, out: Path) -> None:
    positions = json.loads(POSITIONS.read_text(encoding="utf-8"))
    settings = config()["evaluate"]
    meta = _meta(out) | {"think": False, "num_ctx": CONTEXT_TOKENS, "model": model}
    async with httpx.AsyncClient(timeout=120) as client:
        writer = None
        if model is not None:
            tags = (await client.get(f"{url}/api/tags")).json()["models"]
            digests = [m["digest"] for m in tags if m["name"] in (model, f"{model}:latest")]
            if not digests:
                raise SystemExit(f"{model} is not on {url}")
            meta["model_digest"] = digests[0]
            meta["ollama_version"] = (await client.get(f"{url}/api/version")).json()["version"]
            writer = OllamaExplanationAdapter(
                base_url=url, model=model, client=client, context_tokens=CONTEXT_TOKENS
            )
            await writer.rewrite("Reply with one word.", "Ready?")
            loaded = (await client.get(f"{url}/api/ps")).json()["models"]
            meta["size_vram"] = next(
                m["size_vram"] for m in loaded if m["digest"] == meta["model_digest"]
            )
        with out.open("x", encoding="utf-8") as handle:
            for position in positions:
                facts = facts_for(position)
                board = chess.Board(position["fen"])
                played = numbered_san(board, chess.Move.from_uci(position["played"]))
                # The phrase bank is deterministic: one sample per position.
                samples = 1 if writer is None else settings[f"{position['set']}_samples"]
                for n in range(samples):
                    start = time.perf_counter()
                    if writer is None:
                        voiced = phrase_facts(facts)
                    else:
                        voiced = await voice_facts(writer, facts, mover=board.turn, played=played)
                    row = {
                        "arm": arm,
                        "set": position["set"],
                        "test": position["test"],
                        "n": n,
                        "text": voiced.text,
                        "sentences": len(voiced.sentences),
                        "fallback_sentences": voiced.fallback_sentences,
                        "calls": voiced.calls,
                        "seconds": round(time.perf_counter() - start, 3),
                        "meta": meta,
                    }
                    handle.write(json.dumps(row) + "\n")
                    handle.flush()


def board_rows(specs: Sequence[str]) -> list[Row]:
    """Board-path records (``arm=file``) in this evaluation's row shape."""
    rows = []
    for spec in specs:
        arm, _, path = spec.partition("=")
        for row in voice_blind.read_jsonl(Path(path)):
            rows.append(
                row
                | {
                    "arm": arm,
                    "set": "board",
                    "fallback_sentences": row["sentence_fallbacks"],
                }
            )
    return rows


def build_blind(rows: list[Row], seed: int) -> tuple[list[Row], dict[str, Row]]:
    positions = {p["test"]: p for p in json.loads(POSITIONS.read_text(encoding="utf-8"))}
    stored = json.loads(BOARD_LINES.read_text(encoding="utf-8"))
    rows = list(rows)
    random.Random(seed).shuffle(rows)
    items, key = [], {}
    for index, row in enumerate(rows, 1):
        if row["set"] == "board":
            fen = board_path.POSITIONS[row["test"]]
            facts = board_path.judge_facts(board_path.board_facts(stored, row["test"]))
        else:
            fen = positions[row["test"]]["fen"]
            mover = chess.Board(fen).turn
            facts = [
                f"{role_label(f.role, mover)}: {f.text}" for f in facts_for(positions[row["test"]])
            ]
        item = f"f{index:03d}"
        items.append(
            {
                "id": item,
                "position": board_path.render(fen),
                "facts": facts,
                "comment": row["text"],
            }
        )
        key[item] = {k: row[k] for k in ("arm", "set", "test", "n")}
    return items, key


def _interval(share: float, n: int) -> float:
    return 196 * math.sqrt(share * (1 - share) / n)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


SETS = {"heldout": "Held-out games", "benchmark": "#462 game moves", "board": "Analysis Board"}


def summarise(rows: list[Row], key: dict[str, Row], judgements: list[Row]) -> str:
    joined = voice_blind.join(key, judgements)
    out = [
        "| Set | Arm | n | Faithful (±95%) | Faithful and lively | Lively | Clear |"
        " Fallback sentences | p50 s | p95 s | GPU GiB |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    arms = list(dict.fromkeys(row["arm"] for row in rows))
    vram = {r["arm"]: r["meta"]["size_vram"] / 2**30 for r in rows if r["meta"].get("size_vram")}
    for name, label in [*SETS.items(), ("all", "All")]:
        for arm in arms:
            judged = [j for j in joined if j["arm"] == arm and name in (j["set"], "all")]
            records = [r for r in rows if r["arm"] == arm and name in (r["set"], "all")]
            if not judged:
                continue
            n = len(judged)
            faithful = sum(j["faithful"] for j in judged) / n
            both = sum(j["faithful"] and j["lively"] >= 2 for j in judged) / n
            fallbacks = sum(r["fallback_sentences"] for r in records)
            seconds = [r["seconds"] for r in records]
            out.append(
                f"| {label} | {arm} | {n} | {faithful:.0%} ±{_interval(faithful, n):.0f} | "
                f"{both:.0%} | {statistics.mean(j['lively'] for j in judged):.2f} | "
                f"{statistics.mean(j['clear'] for j in judged):.2f} | "
                f"{fallbacks / sum(r['sentences'] for r in records):.1%} | "
                f"{statistics.median(seconds):.2f} | {_p95(seconds):.2f} | "
                f"{vram[arm]:.2f} |"
                if arm in vram
                else f"| {label} | {arm} | {n} | {faithful:.0%} ±{_interval(faithful, n):.0f} | "
                f"{both:.0%} | {statistics.mean(j['lively'] for j in judged):.2f} | "
                f"{statistics.mean(j['clear'] for j in judged):.2f} | "
                f"{fallbacks / sum(r['sentences'] for r in records):.1%} | - | - | 0 |"
            )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("positions")
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--engine", required=True)
    p = sub.add_parser("run")
    p.add_argument("--arm", required=True)
    p.add_argument("--model", help="omit for the phrase bank")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--out", type=Path, required=True)
    for name in ("blind", "summarise"):
        p = sub.add_parser(name)
        p.add_argument("runs", type=Path, nargs="+")
        p.add_argument("--board", action="append", default=[], help="arm=records.jsonl")
        if name == "blind":
            p.add_argument("--out", type=Path, required=True)
            p.add_argument("--key-out", type=Path, required=True)
        else:
            p.add_argument("--key", type=Path, required=True)
            p.add_argument("--judgements", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "positions":
        rows = build_positions(args.data_dir, args.engine)
        POSITIONS.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
        return
    if args.command == "run":
        asyncio.run(run(args.arm, args.model, args.url, args.out))
        return
    rows = [r for path in args.runs for r in voice_blind.read_jsonl(path)]
    rows += board_rows(args.board)
    if args.command == "blind":
        if args.key_out.resolve().is_relative_to(args.out.resolve()):
            raise SystemExit("--key-out must lie outside the judge folder")
        items, key = build_blind(rows, config()["evaluate"]["blind_seed"])
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "items.json").write_text(json.dumps(items, indent=1) + "\n", encoding="utf-8")
        args.key_out.write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
        return
    key = json.loads(args.key.read_text(encoding="utf-8"))
    judgements = json.loads(args.judgements.read_text(encoding="utf-8"))
    print(summarise(rows, key, judgements))


if __name__ == "__main__":
    main()
