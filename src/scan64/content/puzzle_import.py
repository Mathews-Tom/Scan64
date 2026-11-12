"""Operator-only, bounded-memory import of the CC0 Lichess puzzle database."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import chess
import httpx
import zstandard
from chess_lesson_spec.models import Interaction, LessonSpec, Objective, Source, Verification
from sqlalchemy import func
from sqlmodel import Session, col, select

from scan64.content.models import ContentItem
from scan64.content.validate import validate_solution_line

DEFAULT_PUZZLE_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
_REQUIRED_COLUMNS = {
    "PuzzleId",
    "FEN",
    "Moves",
    "Rating",
    "RatingDeviation",
    "Popularity",
    "NbPlays",
    "Themes",
}
_BATCH_SIZE = 256


@dataclass(frozen=True)
class ImportFilters:
    limit: int = 50_000
    min_rating: int = 600
    max_rating: int = 1800
    max_rating_deviation: int = 100
    min_popularity: int = 80
    min_plays: int = 100

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be positive")
        if self.min_rating > self.max_rating:
            raise ValueError("min_rating must not exceed max_rating")
        if self.max_rating_deviation < 0 or self.min_plays < 0:
            raise ValueError("max_rating_deviation and min_plays must be non-negative")


@dataclass(frozen=True)
class SourceProvenance:
    path: str
    sha256: str
    compressed_size: int
    url: str | None = None
    last_modified: str | None = None
    etag: str | None = None
    origin: str = "local"
    integrity_note: str = "Operator-computed SHA256; not upstream authentication."


@dataclass
class ImportSummary:
    provenance: SourceProvenance
    filters: ImportFilters
    read: int = 0
    accepted: int = 0
    inserted: int = 0
    existing: int = 0
    metadata_changed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def underfill(self) -> bool:
        return self.accepted < self.filters.limit

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "underfill": self.underfill,
            "skipped_total": sum(self.skipped.values()),
        }


def local_provenance(path: Path) -> SourceProvenance:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return SourceProvenance(str(path.resolve()), digest.hexdigest(), size)


def download_puzzles(
    output: Path,
    *,
    url: str = DEFAULT_PUZZLE_URL,
    client: httpx.Client | None = None,
) -> SourceProvenance:
    """Save the complete response atomically; leave a previous file intact on failure."""
    output.parent.mkdir(parents=True, exist_ok=True)
    owned_client = client is None
    http = client if client is not None else httpx.Client(follow_redirects=True, timeout=60.0)
    temporary: Path | None = None
    try:
        with http.stream("GET", url) as response:
            response.raise_for_status()
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=f".{output.name}.", suffix=".part", delete=False
            ) as target:
                temporary = Path(target.name)
                for chunk in response.iter_raw():
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                expected = response.headers.get("content-length")
                if expected is not None and size != int(expected):
                    raise ValueError("Incomplete puzzle download: Content-Length does not match")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, output)
            temporary = None
            return SourceProvenance(
                path=str(output.resolve()),
                sha256=digest.hexdigest(),
                compressed_size=size,
                url=url,
                last_modified=response.headers.get("last-modified"),
                etag=response.headers.get("etag"),
                origin="download",
            )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if owned_client:
            http.close()


@dataclass(frozen=True)
class _Puzzle:
    puzzle_id: str
    fen: str
    moves: list[str]
    metadata: dict[str, Any]

    @property
    def item_id(self) -> str:
        return f"lichess:{self.puzzle_id}"


def _parse_row(row: dict[str | None, Any], filters: ImportFilters) -> _Puzzle | str:
    if None in row or any(row.get(key) is None for key in _REQUIRED_COLUMNS):
        return "malformed_row"
    puzzle_id = row["PuzzleId"]
    if not re.fullmatch(r"[A-Za-z0-9]+", puzzle_id):
        return "malformed_row"
    try:
        rating = int(row["Rating"])
        deviation = int(row["RatingDeviation"])
        popularity = int(row["Popularity"])
        plays = int(row["NbPlays"])
    except (ValueError, TypeError):
        return "malformed_row"
    if rating < 0 or deviation < 0 or not -100 <= popularity <= 100 or plays < 0:
        return "malformed_row"
    if not filters.min_rating <= rating <= filters.max_rating:
        return "rating"
    if deviation > filters.max_rating_deviation:
        return "rating_deviation"
    if popularity < filters.min_popularity:
        return "popularity"
    if plays < filters.min_plays:
        return "plays"
    moves = row["Moves"].split()
    if len(moves) < 2 or len(moves) % 2:
        return "invalid_parity"
    if validate_solution_line(row["FEN"], moves) is not None:
        return "invalid_solution"
    return _Puzzle(
        puzzle_id=puzzle_id,
        fen=row["FEN"],
        moves=moves,
        metadata={
            "rating": rating,
            "rating_deviation": deviation,
            "popularity": popularity,
            "plays": plays,
            "themes": row["Themes"].split(),
            "game_url": row.get("GameUrl", ""),
            "opening_tags": (row.get("OpeningTags") or "").split(),
        },
    )


def _materialize(
    puzzle: _Puzzle, sequence: int, provenance: SourceProvenance, filters: ImportFilters
) -> ContentItem:
    board = chess.Board(puzzle.fen)
    board.push_uci(puzzle.moves[0])
    lesson = LessonSpec(
        schema_version="0.2.0",
        lesson_id=puzzle.item_id,
        source=Source(kind="external", fen=board.fen()),
        objective=Objective(type="puzzle", instruction="Find and play the puzzle continuation."),
        interaction=Interaction(input="move", maximum_attempts=3, solution=puzzle.moves[1:]),
        verification=Verification(status="source_verified"),
    )
    return ContentItem(
        id=puzzle.item_id,
        domain="puzzles",
        version="0.2.0",
        payload={
            "puzzle_id": puzzle.puzzle_id,
            "fen": puzzle.fen,
            "moves": puzzle.moves,
            "setup": puzzle.moves[0],
            "import_sequence": sequence,
            "lesson": lesson.model_dump(mode="json", exclude_none=True),
            **puzzle.metadata,
            "provenance": asdict(provenance),
            "filters": asdict(filters),
        },
        provenance=provenance.url or f"Local Lichess puzzle database: {provenance.path}",
        licence="CC0-1.0",
        skill_mapping={},
        difficulty_estimate=float(puzzle.metadata["rating"]),
    )


def read_lesson(item: ContentItem) -> LessonSpec:
    """Read the server-only spec; never return it through a public puzzle DTO."""
    return LessonSpec.model_validate(item.payload["lesson"])


def import_puzzles(
    session: Session,
    path: Path,
    *,
    filters: ImportFilters | None = None,
    provenance: SourceProvenance | None = None,
) -> ImportSummary:
    """Import first qualifying unique identities, including already-present rows in the cap.

    Batches commit independently. Metadata is refreshed without replacing identity,
    lesson, initial provenance or import order. Invalid/conflicting/duplicate rows
    never consume the cap; underfilled input never relaxes filters.
    """
    filters = filters or ImportFilters()
    provenance = provenance or local_provenance(path)
    summary = ImportSummary(provenance, filters)
    skipped: Counter[str] = Counter()
    seen: set[str] = set()
    sequence = (
        session.exec(
            select(func.max(ContentItem.payload["import_sequence"].as_integer())).where(
                ContentItem.domain == "puzzles"
            )
        ).one()
        or 0
    )
    with path.open("rb") as compressed:
        with zstandard.ZstdDecompressor().stream_reader(compressed) as decompressed:
            with io.TextIOWrapper(decompressed, encoding="utf-8-sig", newline="") as text:
                reader = csv.DictReader(text, strict=True)
                if reader.fieldnames is None or not _REQUIRED_COLUMNS.issubset(reader.fieldnames):
                    raise ValueError("Puzzle CSV is missing required header columns")
                if len(set(reader.fieldnames)) != len(reader.fieldnames):
                    raise ValueError("Puzzle CSV has duplicate header columns")
                while summary.accepted < filters.limit:
                    # Count malformed records and resume at the CSV reader's current position.
                    rows: list[_Puzzle | str] = []
                    for _ in range(_BATCH_SIZE):
                        try:
                            source_row = next(reader)
                        except StopIteration:
                            break
                        except csv.Error:
                            rows.append("malformed_row")
                        else:
                            rows.append(_parse_row(source_row, filters))
                    if not rows:
                        break
                    ids = [row.item_id for row in rows if isinstance(row, _Puzzle)]
                    existing = (
                        {
                            item.id: item
                            for item in session.exec(
                                select(ContentItem).where(col(ContentItem.id).in_(ids))
                            )
                        }
                        if ids
                        else {}
                    )
                    for row in rows:
                        if summary.accepted >= filters.limit:
                            break
                        summary.read += 1
                        if isinstance(row, str):
                            skipped[row] += 1
                            continue
                        item = existing.get(row.item_id)
                        if item is not None and (
                            item.domain != "puzzles"
                            or item.payload.get("fen") != row.fen
                            or item.payload.get("moves") != row.moves
                        ):
                            skipped["conflict"] += 1
                            continue
                        if row.item_id in seen:
                            skipped["duplicate"] += 1
                            continue
                        seen.add(row.item_id)
                        summary.accepted += 1
                        if item is None:
                            sequence += 1
                            item = _materialize(row, sequence, provenance, filters)
                            session.add(item)
                            existing[row.item_id] = item
                            summary.inserted += 1
                        else:
                            summary.existing += 1
                            if any(
                                item.payload.get(key) != val for key, val in row.metadata.items()
                            ):
                                summary.metadata_changed += 1
                                item.payload = {
                                    **item.payload,
                                    **row.metadata,
                                    "metadata_provenance": asdict(provenance),
                                }
                                item.difficulty_estimate = float(row.metadata["rating"])
                                session.add(item)
                    session.commit()
    summary.skipped = dict(sorted(skipped.items()))
    return summary
