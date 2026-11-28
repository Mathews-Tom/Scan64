"""Explanation quality benchmark (#403).

  python -m benchmarks.narrator.explanation positions --engine stockfish --out POSITIONS.json
  python -m benchmarks.narrator.explanation run --voice Tal --model M --out tal.jsonl
  python -m benchmarks.narrator.explanation blind A.jsonl ... --seed 403 --out JUDGE_DIR --key-out K
  python -m benchmarks.narrator.explanation summarise [--doc PAGE]

``results/`` is immutable: no command writes there.
"""

import argparse
import json
import sys
from pathlib import Path

from benchmarks.narrator.explanation import judge, positions, run, summarise
from benchmarks.narrator.voice import pipeline
from benchmarks.narrator.voice.__main__ import inside, parse_options
from benchmarks.narrator.voice.blind import read_jsonl

IMMUTABLE = Path(__file__).resolve().parent / "results"


def new_file(text: str) -> Path:
    path = Path(text)
    if inside(path, IMMUTABLE):
        raise SystemExit(f"{path} is inside {IMMUTABLE.name}/: stored measurements are immutable")
    if path.exists():
        raise SystemExit(f"{path} exists: use another name")
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.narrator.explanation")
    sub = parser.add_subparsers(dest="command", required=True)
    pos = sub.add_parser("positions", help="build a position set with an engine")
    pos.add_argument("--engine", required=True)
    pos.add_argument("--out", required=True)
    go = sub.add_parser("run", help="run one arm (one model and one voice)")
    go.add_argument("--voice", required=True, choices=run.VOICES)
    go.add_argument("--model", help="an Ollama model; not for the plain voice")
    go.add_argument("--url", default=pipeline.DEFAULT_URL)
    go.add_argument("--options", default="", help="temperature=1,top_p=0.95,top_k=64")
    go.add_argument("--samples", type=int, default=1)
    go.add_argument("--out", required=True)
    bl = sub.add_parser("blind", help="build the blind set and its key")
    bl.add_argument("records", nargs="+")
    bl.add_argument("--seed", type=int, required=True)
    bl.add_argument("--out", required=True, help="the judge's folder: the items only")
    bl.add_argument("--key-out", required=True, help="the key, outside the judge's folder")
    su = sub.add_parser("summarise", help="print the tables, or write them into a page")
    su.add_argument("--doc")
    args = parser.parse_args(argv)

    if args.command == "positions":
        out = new_file(args.out)
        out.write_text(json.dumps(positions.build(args.engine), indent=1) + "\n")
    elif args.command == "run":
        if args.samples < 1:
            raise SystemExit("--samples must be 1 or more")
        out = new_file(args.out)
        client = (
            pipeline.Client(args.url, args.model, parse_options(args.options))
            if args.model
            else None
        )
        rows = run.run(args.voice, client, args.samples, out)
        with out.open("w", encoding="utf-8") as log:
            for count, row in enumerate(rows, 1):
                log.write(json.dumps(row) + "\n")
                log.flush()
                print(f"{count}: {row['test']} {row['error'] or ''}", file=sys.stderr)
    elif args.command == "blind":
        folder, key_out = Path(args.out), new_file(args.key_out)
        if inside(key_out, folder):
            raise SystemExit("--key-out is inside --out: the judge would see the key")
        items_out = new_file(str(folder / "blind-items.json"))
        items, key = judge.build([r for p in args.records for r in read_jsonl(Path(p))], args.seed)
        folder.mkdir(parents=True, exist_ok=True)
        items_out.write_text(json.dumps(items, indent=1) + "\n")
        key_out.write_text(json.dumps(key, indent=1) + "\n")
    elif args.doc:
        doc = Path(args.doc)
        page = summarise.write_doc(doc.read_text(encoding="utf-8"))
        doc.write_text(page, encoding="utf-8")
    else:
        print(summarise.tables(), end="")


if __name__ == "__main__":
    main()
