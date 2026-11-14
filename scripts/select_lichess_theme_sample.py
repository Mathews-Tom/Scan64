"""Freeze M77's first qualifying rows from a checksum-verified compressed prefix.

Run: uv run python scripts/select_lichess_theme_sample.py PREFIX.csv.zst
No engine or diagnosis code is invoked. The remote file changes: retain the prefix
locally; never substitute a different checksum for the registered sample.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

import chess
import zstandard

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests/fixtures/m77"
SOURCE_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
SOURCE_SHA256 = "b58e19f817b7a8889cd713654a59e8914c3e92f11987b0273e74fb30e7d38b96"
SAMPLE_SIZE = 2000
COLUMNS = [
    "PuzzleId",
    "FEN",
    "Blunder",
    "Rating",
    "RatingDeviation",
    "Popularity",
    "NbPlays",
    "Themes",
]


def select(source: Path) -> None:
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SOURCE_SHA256:
            raise ValueError("Source prefix checksum differs from the pre-registration")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    scanned = 0
    with source.open("rb") as stream:
        with zstandard.ZstdDecompressor().stream_reader(stream) as decompressed:
            reader = csv.DictReader(io.TextIOWrapper(decompressed, encoding="utf-8"))
            for scanned, row in enumerate(reader, start=1):
                try:
                    qualifies = (
                        600 <= int(row["Rating"]) <= 1800
                        and int(row["RatingDeviation"]) <= 100
                        and int(row["Popularity"]) >= 80
                        and int(row["NbPlays"]) >= 100
                    )
                    puzzle_id = row["PuzzleId"]
                    blunder = row["Moves"].split()[0]
                    board = chess.Board(row["FEN"])
                    move = chess.Move.from_uci(blunder)
                    valid = board.is_valid() and move in board.legal_moves
                except (ValueError, KeyError, IndexError):
                    continue
                if not qualifies or not valid or not puzzle_id or puzzle_id in seen:
                    continue
                seen.add(puzzle_id)
                rows.append({key: blunder if key == "Blunder" else row[key] for key in COLUMNS})
                if len(rows) == SAMPLE_SIZE:
                    break
    if len(rows) != SAMPLE_SIZE:
        raise ValueError(f"Prefix underfill: {len(rows)}; do not relax filters")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    sample = output.getvalue().encode()
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "puzzles.csv").write_bytes(sample)
    provenance = {
        "source_url": SOURCE_URL,
        "license": "CC0-1.0",
        "retrieved_utc": "2026-11-14",
        "byte_range_inclusive": [0, 8388607],
        "prefix_bytes": 8388608,
        "prefix_sha256": SOURCE_SHA256,
        "http_content_range": "bytes 0-8388607/309751173",
        "http_last_modified": "Sun, 01 Nov 2026 07:57:40 GMT",
        "http_etag": '"6ae6f0f4-12766d85"',
        "sample_size": SAMPLE_SIZE,
        "source_rows_scanned": scanned,
        "selection": (
            "First 2000 valid unique PuzzleIds in official source-file order; "
            "rating 600..1800 inclusive, rating deviation <=100, popularity >=80, plays >=100; "
            "valid standard-chess FEN and legal Moves[0]. No theme or diagnosis selection."
        ),
        "retained_columns": COLUMNS,
        "blunder_column": "Moves[0] only; continuation and game URLs not retained",
        "sample_sha256": hashlib.sha256(sample).hexdigest(),
        "prefix_note": (
            "A byte-range prefix ends mid-zstd-frame. Streaming stops after the fixed N, "
            "before the incomplete frame; the full compressed file is not required."
        ),
    }
    (DEST / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Frozen {len(rows)} rows after scanning {scanned}; sha256={provenance['sample_sha256']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    select(parser.parse_args().source)
