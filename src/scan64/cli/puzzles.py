"""Explicit puzzle download/import commands; nothing downloads at application startup."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

import httpx
import zstandard
from sqlmodel import Session

from scan64.content.puzzle_import import (
    DEFAULT_PUZZLE_URL,
    ImportFilters,
    ImportProgress,
    ImportSummary,
    download_puzzles,
    import_puzzles,
)
from scan64.persistence.database import create_db_and_tables, engine

_TERMINAL_REDRAW_SECONDS = 0.1
_PIPED_LINE_SECONDS = 5.0


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f}"


def _clock_time(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def describe_progress(progress: ImportProgress, elapsed: float, source: str | None) -> str:
    """One human line for a progress report; ``elapsed`` is seconds since its phase began."""
    if progress.phase == "connect":
        return f"Connecting to {source}…" if source else "Connecting…"
    if progress.phase == "import":
        return (
            f"Importing    {progress.done:,} / {progress.total:,} puzzles kept"
            f"  ·  {progress.rows_read or 0:,} rows read"
        )
    label = "Downloading" if progress.phase == "download" else "Reading    "
    parts = [label]
    if progress.total:
        parts.append(f"{_megabytes(progress.done)} / {_megabytes(progress.total)} MB")
        parts.append(f"{progress.done * 100 // progress.total}%")
    else:
        parts.append(f"{_megabytes(progress.done)} MB")
    speed = progress.done / elapsed if elapsed > 0 else 0.0
    if speed > 0:
        parts.append(f"{speed / 1_000_000:.1f} MB/s")
        if progress.total:
            parts.append(f"{_clock_time((progress.total - progress.done) / speed)} left")
    return "  ".join(parts)


class ProgressRenderer:
    """Write progress to a stream: one updating line per phase on a terminal; when piped,
    plain lines at most once every five seconds and never a carriage return."""

    def __init__(
        self,
        stream: TextIO,
        *,
        source: str | None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = stream
        self._source = source
        self._clock = clock
        self._terminal = stream.isatty()
        self._phase: str | None = None
        self._phase_started = 0.0
        self._last_write: float | None = None
        self._pending: ImportProgress | None = None
        self._line_open = False

    def __call__(self, progress: ImportProgress) -> None:
        now = self._clock()
        if progress.phase != self._phase:
            self._end_line()
            self._phase = progress.phase
            self._phase_started = now
            if self._terminal:
                self._draw(progress, now)
                return
        if self._terminal:
            if self._last_write is not None and now - self._last_write < _TERMINAL_REDRAW_SECONDS:
                self._pending = progress
                return
            self._draw(progress, now)
        elif self._last_write is None or now - self._last_write >= _PIPED_LINE_SECONDS:
            self._stream.write(self._text(progress, now) + "\n")
            self._stream.flush()
            self._last_write = now

    def close(self) -> None:
        """Finish an open terminal line so later output starts on its own line."""
        self._end_line()

    def _text(self, progress: ImportProgress, now: float) -> str:
        return describe_progress(progress, now - self._phase_started, self._source)

    def _draw(self, progress: ImportProgress, now: float) -> None:
        self._stream.write("\r" + self._text(progress, now) + "\x1b[K")
        self._stream.flush()
        self._last_write = now
        self._pending = None
        self._line_open = True

    def _end_line(self) -> None:
        if self._pending is not None:
            self._draw(self._pending, self._clock())
        if self._line_open:
            self._stream.write("\n")
            self._stream.flush()
            self._line_open = False


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
    command.add_argument(
        "--quiet", action="store_true", help="Do not report download and import progress."
    )


def puzzles_command(args: argparse.Namespace) -> int:
    progress = (
        None if args.quiet else ProgressRenderer(sys.stderr, source=urlsplit(args.url).hostname)
    )
    try:
        try:
            summary, filters = _run_import(args, progress)
        finally:
            if progress is not None:
                progress.close()
    except (OSError, ValueError, httpx.HTTPError, zstandard.ZstdError) as error:
        print(f"Puzzle import failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Puzzle import interrupted.", file=sys.stderr)
        return 130
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


def _run_import(
    args: argparse.Namespace, progress: ProgressRenderer | None
) -> tuple[ImportSummary, ImportFilters]:
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
        provenance = download_puzzles(path, url=args.url, progress=progress)
    create_db_and_tables()
    with Session(engine) as session:
        summary = import_puzzles(
            session, path, filters=filters, provenance=provenance, progress=progress
        )
    return summary, filters
