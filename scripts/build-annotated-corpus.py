#!/usr/bin/env python3
"""Build the corpus of human-annotated public-domain games (issue #463).

Downloads each book in ``scripts/annotated-corpus.toml`` once into
``<data-dir>/raw``, stops unless its SHA-256 matches, reads its games, replays
every move with python-chess and ties each comment to the position after the
move it follows. Writes ``games.jsonl`` and ``rejections.jsonl`` to
``<data-dir>/corpus``: book text stays outside the repository. Writes the
manifest and the per-source report into the repository.

Run: ``uv run python scripts/build-annotated-corpus.py --data-dir D:/scan64-corpus``
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import re
import tomllib
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scan64.learning.annotated_corpus.books import BookLayout, read_games
from scan64.learning.annotated_corpus.corpus import Game, Rejection, SourceReport, replay, report

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "scripts" / "annotated-corpus.toml"
USER_AGENT = "scan64-annotated-corpus-build"

log = logging.getLogger("build-annotated-corpus")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fetch(url: str, target: Path, sha256: str) -> None:
    if not target.exists():
        log.info("downloading %s", url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        partial = target.with_suffix(target.suffix + ".part")
        partial.write_bytes(data)
        partial.replace(target)
    actual = _sha256(target)
    if actual != sha256:
        raise RuntimeError(f"{target}: SHA-256 {actual} does not match the manifest's {sha256}")


def _layout(table: dict[str, Any]) -> BookLayout:
    separator = table.get("separator")
    return BookLayout(
        header=re.compile(table["header"]),
        separator=re.compile(separator) if separator is not None else None,
        stop_at_result=bool(table["stop_at_result"]),
        skip=tuple(re.compile(pattern) for pattern in table["skip"]),
    )


def _record(item: Game | Rejection) -> dict[str, Any]:
    return dataclasses.asdict(item)


def _report_markdown(
    rows: list[tuple[str, dict[str, Any], SourceReport | None]], built: str
) -> str:
    columns = ["Source", "Games found", "Replayed", "Rejected", "Comments", "Sentences", "Moves"]
    columns.append('Decided by missing "ch"')
    lines = [
        "# Annotated-game corpus",
        "",
        f"Built {built} by `scripts/build-annotated-corpus.py` from "
        "`scripts/annotated-corpus.toml` (issue #463).",
        "The corpus itself (`games.jsonl`, `rejections.jsonl`) is written outside the repository.",
        "",
        "| " + " | ".join(columns) + " |",
        "|---" + "|---:" * (len(columns) - 1) + "|",
    ]
    for source_id, source, result in rows:
        name = f"{source['title']} ({source['published']})"
        if result is None:
            lines.append(f"| {name} | pending |" + " |" * (len(columns) - 2))
            continue
        rejected = result.games_found - result.games_replayed
        cells = [
            name,
            str(result.games_found),
            str(result.games_replayed),
            f"{rejected} ({rejected / result.games_found:.0%})",
            str(result.comments),
            str(result.sentences),
            str(result.moves),
            str(result.by_missing_check),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## Rejections by reason", ""]
    for source_id, source, result in rows:
        if result is None:
            lines.append(f"- `{source_id}`: pending. {source['pending']}")
            continue
        reasons = ", ".join(f"{kind} {count}" for kind, count in result.rejected.items())
        lines.append(f"- `{source_id}`: {reasons or 'none'}.")
    lines += [
        "",
        "A move converts only when exactly one legal move fits its descriptive text. When a",
        'text fits both a checking and a non-checking move and carries no "ch", the',
        "non-checking move is taken; the last column counts those moves. Any other ambiguity",
        "rejects the whole game.",
        "",
        "Only headed games are read. Endgame and middlegame examples printed without a game",
        "header are not part of the corpus.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, required=True, help="directory outside the repository"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    data_dir: Path = args.data_dir.resolve()
    if data_dir.is_relative_to(ROOT):
        raise SystemExit(
            f"--data-dir {data_dir} is inside the repository; book text must stay outside it"
        )
    raw_dir, corpus_dir = data_dir / "raw", data_dir / "corpus"
    raw_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    with CONFIG.open("rb") as stream:
        config = tomllib.load(stream)
    built = datetime.now(UTC).strftime("%Y-%m-%d")
    manifest: dict[str, Any] = {
        "built": built,
        "config": CONFIG.relative_to(ROOT).as_posix(),
        "sources": {},
    }
    rows: list[tuple[str, dict[str, Any], SourceReport | None]] = []
    games_out = (corpus_dir / "games.jsonl").open("w", encoding="utf-8")
    rejections_out = (corpus_dir / "rejections.jsonl").open("w", encoding="utf-8")
    with games_out, rejections_out:
        for source_id, source in config["sources"].items():
            entry: dict[str, Any] = {
                "title": source["title"],
                "author": source["author"],
                "published": source["published"],
            }
            if "layout" not in source:
                entry["pending"] = source["pending"]
                manifest["sources"][source_id] = entry
                rows.append((source_id, source, None))
                continue
            target = raw_dir / source["url"].rsplit("/", 1)[-1]
            _fetch(source["url"], target, source["sha256"])
            games = read_games(
                target.read_text(encoding=source["encoding"]), _layout(source["layout"])
            )
            expected = source["layout"]["expected_games"]
            if len(games) != expected:
                raise RuntimeError(
                    f"{source_id}: read {len(games)} games, the book has {expected} headers"
                )
            results = [replay(source_id, game) for game in games]
            for result in results:
                out = games_out if isinstance(result, Game) else rejections_out
                out.write(json.dumps(_record(result), ensure_ascii=False) + "\n")
            summary = report(results)
            entry |= {
                "page": source["page"],
                "url": source["url"],
                "sha256": source["sha256"],
                "licence": source["licence"],
                "games_found": summary.games_found,
                "games_replayed": summary.games_replayed,
                "comments": summary.comments,
                "sentences": summary.sentences,
                "rejected": summary.rejected,
            }
            manifest["sources"][source_id] = entry
            rows.append((source_id, source, summary))
            log.info("%s: %s", source_id, summary)

    output = config["output"]
    (ROOT / output["manifest"]).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (ROOT / output["report"]).write_text(_report_markdown(rows, built), encoding="utf-8")
    log.info("wrote %s and %s; corpus in %s", output["manifest"], output["report"], corpus_dir)


if __name__ == "__main__":
    main()
