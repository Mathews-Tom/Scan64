"""Narrator voice benchmark (#458).

  python -m benchmarks.narrator.voice run 8 --out run8.jsonl [--model M] [--samples N] ...
  python -m benchmarks.narrator.voice blind RUN.jsonl [RUN.jsonl ...] --facts run5 --seed 1 \
      --prefix x --out DIR [--halves]
  python -m benchmarks.narrator.voice blind --stored run8 --out DIR
  python -m benchmarks.narrator.voice summarise [--out FILE | --doc PAGE]

The model server is a local Ollama (``--url``, default http://127.0.0.1:11434). Thinking is
always off and the context is 4096 tokens.
"""

import argparse
import json
import sys
from pathlib import Path

from benchmarks.narrator.voice import blind, pipeline
from benchmarks.narrator.voice.summarise import summarise, write_doc


def parse_options(text: str) -> dict[str, float | int]:
    """``temperature=1,top_p=0.95,top_k=64``: temperature and any value with a '.' are floats."""
    options: dict[str, float | int] = {}
    for pair in filter(None, text.split(",")):
        key, value = pair.split("=")
        options[key] = float(value) if "." in value or key == "temperature" else int(value)
    return options


def cmd_run(args: argparse.Namespace) -> None:
    client = pipeline.Client(args.url, args.model, parse_options(args.options))
    styles = args.styles.split(",") if args.styles else None
    tests = args.tests.split(",") if args.tests else None
    out = Path(args.out)
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists; a stored run is immutable (use another name or --force)")
    count = 0
    with out.open("w", encoding="utf-8") as log:
        for record in pipeline.run(client, args.number, args.samples, styles, args.own, tests):
            log.write(json.dumps(record) + "\n")
            log.flush()
            count += 1
            print(
                f"{count}: {record['test']} {record.get('style')} n={record['n']}", file=sys.stderr
            )
    print(f"wrote {count} records to {out}")


def write_set(items: list[list[dict]], key: dict, out: Path, name: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for h, half in enumerate(items):
        suffix = str(h) if len(items) > 1 else ""
        (out / f"{name}{suffix}-items.json").write_text(json.dumps(half, indent=1))
    (out / f"{name}-key.json").write_text(json.dumps(key, indent=1))
    print(f"{sum(len(i) for i in items)} items in {len(items)} set(s), key in {out}")


def cmd_blind(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if args.stored:
        stored = blind.SETS[args.stored]
        items, key = stored.build()
        write_set(items, key, out, stored.name)
        return
    if not args.records or not args.facts or args.seed is None or not args.prefix:
        raise SystemExit("blind needs RECORDS, --facts, --seed and --prefix (or --stored NAME)")
    rows = []
    for path in map(Path, args.records):
        rows += [r | {"run": path.stem} for r in blind.read_jsonl(path)]
    items, key = blind.build(
        rows,
        seed=args.seed,
        prefix=args.prefix,
        facts=args.facts,
        key_fields=("run", "test", "style", "n"),
        halves_by="run" if args.halves else None,
    )
    write_set(items, key, out, "blind")


def cmd_summarise(args: argparse.Namespace) -> None:
    if args.doc:
        doc = Path(args.doc)
        doc.write_text(write_doc(doc.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"tables written to {doc}")
        return
    text = summarise()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.narrator.voice")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one experiment against a local Ollama")
    run.add_argument("number", type=int, choices=range(1, 10), metavar="N", help="run 1-9")
    run.add_argument("--out", required=True)
    run.add_argument("--model", default=pipeline.DEFAULT_MODEL)
    run.add_argument("--url", default=pipeline.DEFAULT_URL)
    run.add_argument("--samples", type=int, help="comments per position and style")
    run.add_argument("--styles", help="comma-separated styles (default: the run's own)")
    run.add_argument("--tests", help="comma-separated positions (default: all)")
    run.add_argument("--options", default="", help="sampling options, e.g. temperature=1,top_k=64")
    run.add_argument("--own", action="store_true", help="run 9: add the 'own words' rule")
    run.add_argument("--force", action="store_true", help="overwrite --out")
    run.set_defaults(func=cmd_run)

    bl = sub.add_parser("blind", help="build a blind judging set and its key")
    bl.add_argument("records", nargs="*")
    bl.add_argument("--stored", choices=sorted(blind.SETS), help="rebuild a stored set")
    bl.add_argument("--facts", choices=sorted(blind.FACTS))
    bl.add_argument("--seed", type=int)
    bl.add_argument("--prefix")
    bl.add_argument("--halves", action="store_true", help="two halves, every run in equal share")
    bl.add_argument("--out", required=True)
    bl.set_defaults(func=cmd_blind)

    su = sub.add_parser("summarise", help="rebuild the results tables from results/")
    su.add_argument("--out")
    su.add_argument("--doc", help="rewrite the tables between the summarise markers of a page")
    su.set_defaults(func=cmd_summarise)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
