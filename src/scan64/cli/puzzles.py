"""Explicit puzzle download/import commands; nothing downloads at application startup."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import zstandard
from sqlmodel import Session

from scan64.content.puzzle_import import (
    DEFAULT_PUZZLE_URL,
    ImportFilters,
    download_puzzles,
    import_puzzles,
)
from scan64.persistence.database import create_db_and_tables, engine


def add_puzzle_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="puzzles_command", required=True)
    command = subparsers.add_parser(
        "import", help="Import a local or explicitly downloaded CSV.zst."
    )
    command.add_argument("path", nargs="?", type=Path, help="Complete local Lichess CSV.zst file.")
    command.add_argument(
        "--download", action="store_true", help="Download the complete file first."
    )
    command.add_argument("--url", default=DEFAULT_PUZZLE_URL, help="Source URL for --download.")
    command.add_argument(
        "--data-dir", type=Path, help="Download directory (default: database directory)."
    )
    command.add_argument("--output", type=Path, help="Download filename or absolute destination.")
    command.add_argument("--limit", type=int, default=50_000)
    command.add_argument("--min-rating", type=int, default=600)
    command.add_argument("--max-rating", type=int, default=1800)
    command.add_argument("--max-rating-deviation", type=int, default=100)
    command.add_argument("--min-popularity", type=int, default=80)
    command.add_argument("--min-plays", type=int, default=100)
    command.add_argument("--json", action="store_true", help="Print the complete summary as JSON.")


def puzzles_command(args: argparse.Namespace) -> int:
    try:
        if bool(args.path) == bool(args.download):
            raise ValueError("Supply a local CSV.zst path OR --download, not both")
        if not args.download and (
            args.output is not None or args.data_dir is not None or args.url != DEFAULT_PUZZLE_URL
        ):
            raise ValueError("--url, --output and --data-dir require --download")
        filters = ImportFilters(
            limit=args.limit,
            min_rating=args.min_rating,
            max_rating=args.max_rating,
            max_rating_deviation=args.max_rating_deviation,
            min_popularity=args.min_popularity,
            min_plays=args.min_plays,
        )
        provenance = None
        path = args.path
        if args.download:
            database_path = engine.url.database
            default_dir = (
                Path(database_path).resolve().parent
                if (
                    engine.url.get_backend_name() == "sqlite"
                    and database_path is not None
                    and database_path != ":memory:"
                )
                else Path("data").resolve()
            )
            data_dir = args.data_dir or Path(os.environ.get("SCAN64_DATA_DIR", str(default_dir)))
            path = data_dir / (args.output or Path("lichess_db_puzzle.csv.zst"))
            provenance = download_puzzles(path, url=args.url)
        create_db_and_tables()
        with Session(engine) as session:
            summary = import_puzzles(session, path, filters=filters, provenance=provenance)
    except (OSError, ValueError, httpx.HTTPError, zstandard.ZstdError) as error:
        print(f"Puzzle import failed: {error}", file=sys.stderr)
        return 1
    data = summary.to_dict()
    if args.json:
        print(json.dumps(data, sort_keys=True))
    else:
        print("Provenance: " + json.dumps(data["provenance"], sort_keys=True))
        print("Filters: " + json.dumps(data["filters"], sort_keys=True))
        print(
            f"Read {summary.read}; accepted {summary.accepted} unique rows "
            f"(inserted {summary.inserted}, existing {summary.existing}); "
            f"metadata_changed {summary.metadata_changed}."
        )
        print("Skipped by reason: " + json.dumps(summary.skipped, sort_keys=True))
        if summary.underfill:
            print(
                f"Underfill: {summary.accepted} of {filters.limit} requested; "
                "filters were not relaxed."
            )
    return 0
