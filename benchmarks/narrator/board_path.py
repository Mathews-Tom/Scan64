"""Analysis Board path of the narrator on the held-out positions H1 and H3-H5 (#462).

The app pipeline, end to end: Stockfish searches each position as the
Analysis Board does (``HINT_NODES``, ``HINT_MULTIPV``, the first
``FREE_ANALYSIS_LINES`` lines, ``LINE_NODES`` per position along them), then
``resolve_board_lines_explanation`` words the lines with the voice pipeline
(#454) through a local Ollama, thinking off.

Commands, from the repository root:

    python -m benchmarks.narrator.board_path lines --engine <stockfish> --out lines.json
    python -m benchmarks.narrator.board_path run --lines lines.json --out run.jsonl
    python -m benchmarks.narrator.board_path blind run.jsonl --lines lines.json \
        --out judge/ --key-out key.json
    python -m benchmarks.narrator.board_path summarise --key key.json \
        --judgements judge/board-judgements.json --runs run.jsonl

The engine lines are searched once and stored, so every sample words the same
facts. The blind items use the #458 format (``id``, ``position``, ``facts``,
``comment``); the judge folder holds only the items, and the key lies outside it.
The judge sees the facts the app gave the model: the template's opening
sentence and the role facts of each line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import chess
import chess.engine
import httpx

from scan64.api.insights import FREE_ANALYSIS_LINES
from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.assembly import narrator_counts
from scan64.explanations.board_lines import (
    board_line_positions,
    build_board_lines_facts,
    line_scores,
    resolve_board_lines_explanation,
    scored_legal_lines,
    template_board_explanation,
)
from scan64.explanations.facts import LINE_NODES
from scan64.explanations.role_facts import role_facts, role_label
from scan64.providers.llm.adapters import OllamaExplanationAdapter
from scan64.providers.stockfish.pool import HINT_MULTIPV, HINT_NODES

Row = dict[str, Any]

# The held-out positions of #458 (benchmarks/narrator/voice/data.py, FENS_HELDOUT).
# H2-skewer ("8/1r6/8/3k4/8/8/6B1/6K1 w - - 0 1") is left out: the bishop on g2
# already gives check to the king on d5 with White to move, so the position is
# illegal, the Analysis Board rejects it, and Stockfish crashes on it.
POSITIONS: dict[str, str] = {
    "H1-missed-mate": "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
    "H3-stalemate": "7k/8/6K1/8/8/8/8/5Q2 w - - 0 1",
    "H4-pin": "4k3/8/8/4n3/8/8/5P2/4R1K1 w - - 0 1",
    "H5-allowed-fork": "6k1/8/8/8/3n4/8/6PP/3Q2K1 w - - 0 1",
}
DEFAULT_MODEL = "scan64-bench-gemma4-e4b-q4ks:latest"
DEFAULT_URL = "http://127.0.0.1:11434"
CONTEXT_TOKENS = 4096

NAMES = {
    chess.KING: "king",
    chess.QUEEN: "queen",
    chess.ROOK: "rook",
    chess.BISHOP: "bishop",
    chess.KNIGHT: "knight",
    chess.PAWN: "pawn",
}
ORDER = (chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN)


def render(fen: str) -> str:
    """The position in plain words, as in the #458 blind items."""
    board = chess.Board(fen)
    lines = []
    for color, side in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
        parts = []
        for piece_type in ORDER:
            squares = [chess.square_name(s) for s in sorted(board.pieces(piece_type, color))]
            if squares:
                joined = (
                    squares[0]
                    if len(squares) == 1
                    else ", ".join(squares[:-1]) + " and " + squares[-1]
                )
                parts.append(f"{NAMES[piece_type]}{'s' if len(squares) > 1 else ''} on {joined}")
        lines.append(f"{side}: " + "; ".join(parts) + ".")
    lines.append(("White" if board.turn == chess.WHITE else "Black") + " to move.")
    return "\n".join(lines)


# --- Engine lines --------------------------------------------------------------


def _raw(info: chess.engine.InfoDict, turn: chess.Color) -> Row:
    score = info["score"].pov(turn)
    return {
        "pv": [move.uci() for move in info.get("pv", [])],
        "score_cp": score.score(),
        "score_mate": score.mate(),
        "depth": info.get("depth"),
        "seldepth": info.get("seldepth"),
    }


def _search(engine_path: str, fen: str) -> Row:
    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        engine.configure({"Threads": 1, "Hash": 16})
        board = chess.Board(fen)
        infos = engine.analyse(board, chess.engine.Limit(nodes=HINT_NODES), multipv=HINT_MULTIPV)
        raw = [_raw(info, board.turn) for info in infos]
        lines = scored_legal_lines(board, EngineAnalysis(position_id=uuid4(), raw_result=raw))
        along = []
        for position in board_line_positions(board, lines[:FREE_ANALYSIS_LINES]):
            info = engine.analyse(position, chess.engine.Limit(nodes=LINE_NODES))
            along.append(
                {
                    "fen": position.fen(),
                    "moves": [move.uci() for move in position.move_stack],
                    "raw": [_raw(info, position.turn)],
                }
            )
        return {"fen": fen, "engine": engine.id.get("name"), "raw": raw, "along": along}


def search_lines(engines: list[str]) -> Row:
    """For each position: the board's MultiPV search and the searches along its lines.

    A position whose search kills the first engine is searched with the next one.
    """
    out: Row = {"hint_nodes": HINT_NODES, "line_nodes": LINE_NODES}
    for test, fen in POSITIONS.items():
        for engine_path in engines:
            try:
                out[test] = _search(engine_path, fen)
                break
            except chess.engine.EngineTerminatedError:
                print(f"{test}: {engine_path} died; trying the next engine", file=sys.stderr)
        else:
            raise SystemExit(f"{test}: every engine died")
    return out


def board_facts(stored: Row, test: str) -> Any:
    entry = stored[test]
    board = chess.Board(entry["fen"])
    lines = scored_legal_lines(board, EngineAnalysis(position_id=uuid4(), raw_result=entry["raw"]))[
        :FREE_ANALYSIS_LINES
    ]
    positions = board_line_positions(board, lines)
    by_moves = {tuple(a["moves"]): a["raw"] for a in entry["along"]}
    analyses = [
        EngineAnalysis(
            position_id=uuid4(), raw_result=by_moves[tuple(m.uci() for m in p.move_stack)]
        )
        for p in positions
    ]
    return build_board_lines_facts(board, lines, line_scores(positions, analyses))


def judge_facts(facts: Any) -> list[str]:
    """The facts the app gives the model, as the judge reads them."""
    board = chess.Board(facts.position.fen)
    out = [f"Opening: {template_board_explanation(facts).claims[0].text}"]
    for line in facts.lines:
        if not line.uci:
            continue
        story = role_facts(facts.position.fen, line.uci[0], line.uci[0], played_line=line.uci[1:])
        out += [f"Line {line.rank}, {role_label(f.role, board.turn)}: {f.text}" for f in story]
    return out


# --- Run -----------------------------------------------------------------------


class CountingWriter(OllamaExplanationAdapter):
    """The app's Ollama adapter, counting the sentence calls."""

    calls = 0

    async def rewrite(self, system: str, user: str) -> str:
        CountingWriter.calls += 1
        return await super().rewrite(system, user)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    ).stdout.strip()


async def run(stored: Row, *, model: str, url: str, samples: int, out: Path) -> None:
    meta = {
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "model": model,
        "think": False,
        "num_ctx": CONTEXT_TOKENS,
        "engines": {t: stored[t]["engine"] for t in POSITIONS},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        tags = (await client.get(f"{url}/api/tags")).json()
        digest = next((m["digest"] for m in tags["models"] if m["name"] == model), None)
        if digest is None:
            raise SystemExit(f"{model} is not on {url}")
        meta["model_digest"] = digest
        meta["ollama_version"] = (await client.get(f"{url}/api/version")).json().get("version")
        writer = CountingWriter(
            base_url=url, model=model, client=client, context_tokens=CONTEXT_TOKENS
        )
        with out.open("x", encoding="utf-8") as handle:
            for test in POSITIONS:
                facts = board_facts(stored, test)
                for n in range(samples):
                    before = narrator_counts()["board"]
                    CountingWriter.calls = 0
                    start = time.perf_counter()
                    resolved = await resolve_board_lines_explanation(
                        facts, local_provider=writer, environment={}
                    )
                    seconds = time.perf_counter() - start
                    after = narrator_counts()["board"]
                    row = {
                        "test": test,
                        "n": n,
                        "model": model,
                        "source": resolved.source,
                        "fallback_reason": resolved.fallback_reason,
                        "text": resolved.explanation.text,
                        "sentences": after.sentences - before.sentences,
                        "sentence_fallbacks": after.sentence_fallbacks - before.sentence_fallbacks,
                        "facts_dropped": after.facts_dropped - before.facts_dropped,
                        "calls": CountingWriter.calls,
                        "seconds": round(seconds, 3),
                        "meta": meta,
                    }
                    handle.write(json.dumps(row) + "\n")
                    handle.flush()
                    print(f"{test} {n} {resolved.source} {seconds:.2f}s", file=sys.stderr)


# --- Blind set and summary -----------------------------------------------------


def read_jsonl(path: Path) -> list[Row]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def blind(
    rows: list[Row], stored: Row, *, seed: int, prefix: str
) -> tuple[list[Row], dict[str, Row]]:
    """Shuffled items with no labels, and the key that unblinds them."""
    facts = {test: judge_facts(board_facts(stored, test)) for test in POSITIONS}
    rows = list(rows)
    random.Random(seed).shuffle(rows)
    items, key = [], {}
    for i, row in enumerate(rows, 1):
        item_id = f"{prefix}{i:03d}"
        items.append(
            {
                "id": item_id,
                "position": render(POSITIONS[row["test"]]),
                "facts": facts[row["test"]],
                "comment": row["text"],
            }
        )
        key[item_id] = {"test": row["test"], "n": row["n"], "model": row["model"]}
    return items, key


def _interval(share: float, n: int) -> float:
    return 1.96 * math.sqrt(share * (1 - share) / n) if n else 0.0


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def summarise(key: dict[str, Row], judgements: list[Row], runs: list[Row]) -> str:
    judged = {j["id"]: j for j in judgements}
    if set(judged) != set(key):
        raise SystemExit("the judgements do not match the key")
    joined = [key[i] | judged[i] for i in key]
    n = len(joined)
    faithful = sum(j["faithful"] for j in joined) / n
    lively = sum(j["faithful"] and j["lively"] >= 2 for j in joined) / n
    sentences = sum(r["sentences"] for r in runs)
    seconds = [r["seconds"] for r in runs]
    out = [
        "| Model | n | Faithful (±95%) | Faithful and lively | Lively | Clear | Jargon | "
        "Narrator source | Fallback sentences | Calls per explanation | p50 s | p95 s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        f"| gemma4-e4b Q4_K_S | {n} | {faithful:.0%} ±{_interval(faithful, n) * 100:.0f} | "
        f"{lively:.0%} | {statistics.mean(j['lively'] for j in joined):.2f} | "
        f"{statistics.mean(j['clear'] for j in joined):.2f} | "
        f"{sum(j['jargon'] for j in joined)} | "
        f"{sum(r['source'] == 'narrator' for r in runs)}/{len(runs)} | "
        f"{sum(r['sentence_fallbacks'] for r in runs) / sentences:.1%} | "
        f"{statistics.mean(r['calls'] for r in runs):.1f} | "
        f"{statistics.median(seconds):.2f} | {_p95(seconds):.2f} |",
        "",
        "| Position | Faithful | Faithful and lively |",
        "|---|---|---|",
    ]
    for test in POSITIONS:
        cell = [j for j in joined if j["test"] == test]
        out.append(
            f"| {test} | {sum(j['faithful'] for j in cell)}/{len(cell)} | "
            f"{sum(j['faithful'] and j['lively'] >= 2 for j in cell)}/{len(cell)} |"
        )
    errors = [f"- {j['test']}: {j['error']}" for j in joined if not j["faithful"]]
    return "\n".join(out + ["", "Errors:", *errors])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.narrator.board_path")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("lines")
    p.add_argument("--engine", action="append", required=True, help="repeat for fallbacks")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("run")
    p.add_argument("--lines", type=Path, required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--samples", type=int, default=25)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("blind")
    p.add_argument("runs", type=Path, nargs="+")
    p.add_argument("--lines", type=Path, required=True)
    p.add_argument("--seed", type=int, default=462)
    p.add_argument("--prefix", default="b")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--key-out", type=Path, required=True)
    p = sub.add_parser("summarise")
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--judgements", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    args = parser.parse_args(argv)

    if args.command == "lines":
        args.out.write_text(
            json.dumps(search_lines(args.engine), indent=1) + "\n", encoding="utf-8"
        )
    elif args.command == "run":
        stored = json.loads(args.lines.read_text(encoding="utf-8"))
        asyncio.run(run(stored, model=args.model, url=args.url, samples=args.samples, out=args.out))
    elif args.command == "blind":
        if args.key_out.resolve().is_relative_to(args.out.resolve()):
            raise SystemExit("--key-out must lie outside the judge folder")
        stored = json.loads(args.lines.read_text(encoding="utf-8"))
        rows = [row for path in args.runs for row in read_jsonl(path)]
        items, key = blind(rows, stored, seed=args.seed, prefix=args.prefix)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "board-items.json").write_text(
            json.dumps(items, indent=1) + "\n", encoding="utf-8"
        )
        args.key_out.write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    else:
        key = json.loads(args.key.read_text(encoding="utf-8"))
        judgements = json.loads(args.judgements.read_text(encoding="utf-8"))
        runs = [row for path in args.runs for row in read_jsonl(path)]
        print(summarise(key, judgements, runs))


if __name__ == "__main__":
    main()
