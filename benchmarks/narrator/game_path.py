"""Game-move path of the narrator on many held-out positions, every candidate model (#505).

One mistake from each recorded public game (``tests/fixtures/m48``), one comment per model and
position, through the app's own voice pipeline (``role_facts`` and ``voice_facts``, #454), with
the app's Ollama adapter and thinking off. Each comment has its own game, so the 95% (Wilson)
interval over comments is also the interval over positions. Games that ``positions.json`` uses
are left out, and none is a position of the #458 voice benchmark.

Commands, from the repository root:

    python -m benchmarks.narrator.game_path positions --engine <stockfish> --out positions.json
    python -m benchmarks.narrator.game_path run --positions positions.json --model <m> \
        --name <arm> --out run-<arm>.jsonl
    python -m benchmarks.narrator.game_path blind run-*.jsonl --positions positions.json \
        --start 0 --count 120 --out judge/ --key-out key.json
    python -m benchmarks.narrator.game_path summarise --key key.json \
        --judgements judge/verdicts-*.json --runs run-*.jsonl --positions positions.json

The blind items follow #458 (position, facts, comment, shuffled, no labels). The comments of one
position share its text, so a batch lists the position and the facts once. Two control arms test
the judge: the plain facts (no model, always true) and corrupted plain facts (always false).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import chess
import chess.engine
import chess.pgn
import httpx

from benchmarks.narrator.board_path import (
    CONTEXT_TOKENS,
    DEFAULT_URL,
    CountingWriter,
    _p95,
    engine_identity,
    read_jsonl,
    render,
    tree_state,
)
from scan64.explanations.role_facts import (
    RoleFact,
    RoleFactsError,
    numbered_san,
    role_facts,
    role_label,
)
from scan64.explanations.sentence_check import finish
from scan64.explanations.voice_pipeline import select_facts, voice_facts
from scan64.providers.llm.adapters import LLMProviderError

Row = dict[str, Any]
ROOT = Path(__file__).resolve().parents[2]
PGNS = sorted((ROOT / "tests/fixtures/m48").glob("*real_games.pgn"))
SEED = 505
SCAN_NODES, NODES = 20_000, 200_000
MIN_DROP_CP, MAX_EVAL_CP = 150, 600
PLAIN, FALSE = "plain-facts", "control-false"
LINE_MOVES = 8
# The #458 measure of a square and a move.
SQUARE = re.compile(r"\b[a-h][1-8]\b")
MOVE = re.compile(
    r"\b(?:O-O(?:-O)?|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8]|[a-h][1-8](?==|\b))"
)


def story_of(entry: Row) -> tuple[RoleFact, ...]:
    """Every fact of the mistake, as ``lesson_role_facts`` gives them to the pipeline."""
    return role_facts(
        entry["fen"],
        entry["played"],
        entry["best_line"][0],
        best_line=entry["best_line"],
        played_line=entry["played_line"],
    )


def facts_of(entry: Row) -> tuple[RoleFact, ...]:
    """The merged facts the pipeline voices, one per role, in story order."""
    return select_facts(story_of(entry))[0]


def fact_lines(entry: Row) -> list[str]:
    mover = chess.Board(entry["fen"]).turn
    return [f"{role_label(f.role, mover)}: {f.text}" for f in facts_of(entry)]


# --- Positions -------------------------------------------------------------------


def _cp(score: chess.engine.PovScore, side: chess.Color) -> int:
    return score.pov(side).score(mate_score=10_000)


def _mistake(engine: chess.engine.SimpleEngine, game: chess.pgn.Game) -> Row | None:
    """A seeded choice of one move of the game that Stockfish confirms as a mistake."""
    site = game.headers["Site"]
    board, plies = game.board(), []
    for move in game.mainline_moves():
        if board.ply() >= 10:
            plies.append((board.copy(stack=False), move))
        board.push(move)
    random.Random(f"{SEED}:{site}").shuffle(plies)
    for position, move in plies:
        for nodes in (SCAN_NODES, NODES):
            limit = chess.engine.Limit(nodes=nodes)
            before = engine.analyse(position, limit, game=object())
            best = before.get("pv", [])
            value = _cp(before["score"], position.turn)
            if not best or best[0] == move or abs(value) > MAX_EVAL_CP:
                break
            after = position.copy(stack=False)
            after.push(move)
            if after.is_game_over():
                break
            reply = engine.analyse(after, limit, game=object())
            if value - _cp(reply["score"], position.turn) < MIN_DROP_CP:
                break
        else:
            entry: Row = {
                "id": site.rsplit("/", 1)[-1] + f"-{position.ply()}",
                "game": site,
                "fen": position.fen(),
                "played": move.uci(),
                "best_line": [m.uci() for m in best[:LINE_MOVES]],
                "played_line": [m.uci() for m in reply.get("pv", [])[:LINE_MOVES]],
            }
            try:
                if len(facts_of(entry)) >= 2:
                    return entry
            except RoleFactsError:
                continue
    return None


def positions(engine_path: str, workers: int) -> Row:
    benchmark = json.loads((Path(__file__).parent / "positions.json").read_text(encoding="utf-8"))
    used = {p["game_id"] for p in benchmark["positions"]}
    games: dict[str, chess.pgn.Game] = {}
    for path in PGNS:
        with path.open(encoding="utf-8") as handle:
            while game := chess.pgn.read_game(handle):
                site = game.headers["Site"]
                if site not in used and game.headers.get("Variant", "Standard") == "Standard":
                    games.setdefault(site, game)

    def search(game: chess.pgn.Game) -> Row | None:
        with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
            engine.configure({"Threads": 1, "Hash": 16})
            return _mistake(engine, game)

    with ThreadPoolExecutor(workers) as pool:
        found = [e for e in pool.map(search, games.values()) if e is not None]
    random.Random(SEED).shuffle(found)
    for entry in found:
        if not chess.Board(entry["fen"]).is_valid():
            raise SystemExit(f"illegal position {entry['id']}")
    return {
        "source": [p.relative_to(ROOT).as_posix() for p in PGNS],
        "engine": engine_identity(engine_path),
        "nodes": NODES,
        "seed": SEED,
        "games_read": len(games),
        "positions": found,
    }


# --- Run ---------------------------------------------------------------------------


def _gpu_processes() -> dict[str, int]:
    """GPU memory in MiB per process, as ``nvidia-smi`` reports it."""
    query = ["--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"]
    out = subprocess.run(["nvidia-smi", *query], capture_output=True, text=True, check=False).stdout
    pairs = [line.split(",") for line in out.splitlines() if line.count(",") == 1]
    return {pid.strip(): int(mib) for pid, mib in pairs if mib.strip().isdigit()}


async def run(data: Row, *, name: str, model: str, url: str, count: int, out: Path) -> None:
    commit, dirty, dirty_sha256 = tree_state(out)
    meta: Row = {
        "commit": commit,
        "dirty": dirty,
        "dirty_sha256": dirty_sha256,
        "model": model,
        "think": False,
        "num_ctx": CONTEXT_TOKENS,
        "sampling": "the model's Modelfile",
        "positions_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
    }
    async with httpx.AsyncClient(timeout=180) as client:
        tags = (await client.get(f"{url}/api/tags")).json()["models"]
        names = {model, f"{model}:latest"}
        meta["model_digest"] = next((m["digest"] for m in tags if m["name"] in names), None)
        if meta["model_digest"] is None:
            raise SystemExit(f"{model} is not on {url}")
        meta["ollama_version"] = (await client.get(f"{url}/api/version")).json()["version"]
        shown = (await client.post(f"{url}/api/show", json={"model": model})).json()
        meta["modelfile_parameters"] = shown.get("parameters")
        writer = CountingWriter(
            base_url=url, model=model, client=client, context_tokens=CONTEXT_TOKENS
        )
        gpu_before = _gpu_processes()
        await writer.rewrite("Reply with one word.", "Hello")  # load, so latency excludes it
        loaded = (await client.get(f"{url}/api/ps")).json()["models"]
        meta["size_vram"] = next((m["size_vram"] for m in loaded if m["name"] in names), None)
        meta["loaded_models"] = len(loaded)
        # The process this load started; None when the model was already loaded.
        new = [mib for pid, mib in _gpu_processes().items() if pid not in gpu_before]
        meta["process_mib"] = new[0] if len(new) == 1 else None
        with out.open("x", encoding="utf-8") as handle:
            for entry in data["positions"][:count]:
                board = chess.Board(entry["fen"])
                played = numbered_san(board, chess.Move.from_uci(entry["played"]))
                facts = story_of(entry)
                CountingWriter.calls = 0
                start = time.perf_counter()
                row: Row = {"id": entry["id"], "model": name}
                try:
                    voiced = await voice_facts(writer, facts, mover=board.turn, played=played)
                    row |= {
                        "text": voiced.text,
                        "sentences": len(voiced.sentences),
                        "fallback_sentences": voiced.fallback_sentences,
                        "facts_dropped": voiced.dropped_facts,
                        "error": None,
                    }
                except LLMProviderError as error:
                    row |= {"text": None, "error": str(error)}
                row["calls"] = CountingWriter.calls
                row["seconds"] = round(time.perf_counter() - start, 3)
                if handle.tell() == 0:
                    row["meta"] = meta  # every row of a file has the meta of its first row
                handle.write(json.dumps(row) + "\n")
                handle.flush()
    print(f"{name}: {count} positions", file=sys.stderr)


# --- Blind set -----------------------------------------------------------------------


def corrupt(entry: Row, rng: random.Random) -> str:
    """The plain facts with one certain error: a square that no piece or move can fit."""
    text = " ".join(finish(f.text) for f in facts_of(entry))
    first = SQUARE.search(text)
    assert first is not None
    board = chess.Board(entry["fen"])
    named = set(SQUARE.findall(text))
    empty = [
        s
        for s in chess.SQUARE_NAMES
        if s not in named and not board.piece_at(chess.parse_square(s))
    ]
    wrong = rng.choice(empty)
    # The last square named, so the error lies in the story and not in the first move.
    last = list(SQUARE.finditer(text))[-1]
    return text[: last.start()] + wrong + text[last.end() :]


def blind(
    rows: list[Row],
    data: Row,
    *,
    start: int,
    count: int | dict[str, int],
    seed: int,
    per_batch: int,
) -> tuple[list[list[Row]], dict[str, Row]]:
    """Batches of positions, each with its comments shuffled and unlabelled, and the key."""
    rng = random.Random(seed + start)
    by_position: dict[str, list[Row]] = {}
    for row in rows:
        by_position.setdefault(row["id"], []).append(row)
    ends = count if isinstance(count, dict) else dict.fromkeys({r["model"] for r in rows}, count)
    last = max(ends.values(), default=0)
    batches: list[list[Row]] = []
    key: dict[str, Row] = {}
    chosen = data["positions"][start : start + last]
    for index, entry in enumerate(chosen):
        arms = [
            (r["model"], r["text"])
            for r in by_position.get(entry["id"], [])
            if r["text"] is not None and index < ends.get(r["model"], 0)
        ]
        arms.append((PLAIN, " ".join(finish(f.text) for f in facts_of(entry))))
        if index % 2 == 0:
            arms.append((FALSE, corrupt(entry, rng)))
        rng.shuffle(arms)
        comments = []
        for model, text in arms:
            item_id = f"g{start + index:03d}-{len(comments) + 1:02d}"
            comments.append({"id": item_id, "comment": text})
            key[item_id] = {"position": entry["id"], "model": model}
        if index % per_batch == 0:
            batches.append([])
        batches[-1].append(
            {"position": render(entry["fen"]), "facts": fact_lines(entry), "comments": comments}
        )
    return batches, key


# --- Summary ---------------------------------------------------------------------------


def wilson(successes: int, n: int) -> tuple[float, float]:
    """The 95% Wilson interval of a share."""
    if not n:
        return 0.0, 1.0
    z = 1.96
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def join(key: dict[str, Row], judgements: list[Row]) -> list[Row]:
    judged = {j["id"]: j for j in judgements}
    if len(judged) != len(judgements) or set(judged) != set(key):
        raise SystemExit("the judgements do not match the key")
    for j in judgements:
        if not all(isinstance(j[f], bool) for f in ("faithful", "jargon")) or not all(
            j[f] in (1, 2, 3) for f in ("lively", "clear")
        ):
            raise SystemExit(f"{j['id']}: a field is outside the rubric")
    return [key[i] | judged[i] for i in key]


def _tokens(text: str) -> set[str]:
    moves = set(MOVE.findall(text))
    return moves | set(SQUARE.findall(text)) | {m[-2:] for m in moves if m[0] != "O"}


def coverage(rows: list[Row], data: Row) -> float:
    """The share of the facts' moves and squares that the comments keep (#458's measure)."""
    entries = {e["id"]: e for e in data["positions"]}
    kept = total = 0
    for row in rows:
        if row["text"] is None:
            continue
        want = _tokens(" ".join(f.text for f in facts_of(entries[row["id"]])))
        kept += len(want & _tokens(row["text"]))
        total += len(want)
    return kept / total if total else 0.0


def summarise(key: dict[str, Row], judgements: list[Row], runs: list[Row], data: Row) -> str:
    joined = join(key, judgements)
    out = [
        "| Arm | GPU GiB | Process MiB | n | Faithful | 95% interval | Half-width | "
        "Faithful and lively | Lively | Clear | Jargon | Coverage | Fallback sentences | "
        "Calls per explanation | p50 s | p95 s | Errors |",
        "|" + "---|" * 17,
    ]
    meta = {r["model"]: r["meta"] for r in runs if "meta" in r}
    order = sorted(meta, key=lambda m: meta[m]["size_vram"] or 0)
    for model in [PLAIN, FALSE, *order]:
        cell = [j for j in joined if j["model"] == model]
        if not cell:
            continue
        n, good = len(cell), sum(j["faithful"] for j in cell)
        low, high = wilson(good, n)
        lively = sum(j["faithful"] and j["lively"] >= 2 for j in cell) / n
        row = [
            model,
            f"{meta[model]['size_vram'] / 2**30:.2f}" if model in meta else "0",
            str(meta[model]["process_mib"] or "-") if model in meta else "-",
            str(n),
            f"{good / n:.1%}",
            f"{low:.1%}-{high:.1%}",
            f"{(high - low) / 2 * 100:.1f}",
            f"{lively:.0%}",
            f"{statistics.mean(j['lively'] for j in cell):.2f}",
            f"{statistics.mean(j['clear'] for j in cell):.2f}",
            str(sum(j["jargon"] for j in cell)),
        ]
        mine = [r for r in runs if r["model"] == model]
        done = [r for r in mine if r["error"] is None]
        if done:
            seconds = [r["seconds"] for r in done]
            fallbacks = sum(r["fallback_sentences"] for r in done)
            row += [
                f"{coverage(done, data):.1%}",
                f"{fallbacks / sum(r['sentences'] for r in done):.1%}",
                f"{statistics.mean(r['calls'] for r in done):.1f}",
                f"{statistics.median(seconds):.2f}",
                f"{_p95(seconds):.2f}",
                str(len(mine) - len(done)),
            ]
        else:
            row += ["-"] * 6
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.narrator.game_path")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("positions")
    p.add_argument("--engine", required=True)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("run")
    p.add_argument("--positions", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--count", type=int, default=400)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("blind")
    p.add_argument("runs", type=Path, nargs="+")
    p.add_argument("--positions", type=Path, required=True)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", required=True, help="N for every arm, or a=N,b=N")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--per-batch", type=int, default=4)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--key-out", type=Path, required=True)
    p = sub.add_parser("summarise")
    p.add_argument("--key", type=Path, nargs="+", required=True)
    p.add_argument("--judgements", type=Path, nargs="+", required=True)
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    p.add_argument("--positions", type=Path, required=True)
    args = parser.parse_args(argv)

    def write(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")

    if args.command == "positions":
        write(args.out, positions(args.engine, args.workers))
        return
    data = json.loads(args.positions.read_text(encoding="utf-8"))
    if args.command == "run":
        asyncio.run(
            run(
                data, name=args.name, model=args.model, url=args.url, count=args.count, out=args.out
            )
        )
    elif args.command == "blind":
        if args.key_out.resolve().is_relative_to(args.out.resolve()):
            raise SystemExit("--key-out must lie outside the judge folder")
        rows = [row for path in args.runs for row in read_jsonl(path)]
        count: int | dict[str, int] = (
            {k: int(v) for k, v in (part.split("=") for part in args.count.split(","))}
            if "=" in args.count
            else int(args.count)
        )
        batches, key = blind(
            rows, data, start=args.start, count=count, seed=args.seed, per_batch=args.per_batch
        )
        args.out.mkdir(parents=True, exist_ok=True)
        for i, batch in enumerate(batches):
            write(args.out / f"batch-{args.start:03d}-{i:02d}.json", batch)
        write(args.key_out, key)
    else:
        key: dict[str, Row] = {}
        for path in args.key:
            key |= json.loads(path.read_text(encoding="utf-8"))
        judgements = [j for path in args.judgements for j in json.loads(path.read_text())]
        runs = [row for path in args.runs for row in read_jsonl(path)]
        print(summarise(key, judgements, runs, data))


if __name__ == "__main__":
    main()
