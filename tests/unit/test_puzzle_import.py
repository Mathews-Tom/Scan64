from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import chess
import httpx
import pytest
import zstandard
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from scan64.cli import puzzles as puzzles_cli
from scan64.cli.main import main
from scan64.content.models import ContentItem, PuzzleTheme
from scan64.content.puzzle_import import (
    DEFAULT_PUZZLE_URL,
    ImportFilters,
    ImportProgress,
    ProgressCallback,
    SourceProvenance,
    download_puzzles,
    import_puzzles,
    read_lesson,
)
from scan64.desktop_import import ImportCancelled, run_import

HEADERS = [
    "PuzzleId",
    "FEN",
    "Moves",
    "Rating",
    "RatingDeviation",
    "Popularity",
    "NbPlays",
    "Themes",
    "GameUrl",
    "OpeningTags",
]


def puzzle(puzzle_id: str = "abc12", **changes: str | int) -> dict[str, str]:
    row = dict(
        zip(
            HEADERS,
            [
                puzzle_id,
                chess.STARTING_FEN,
                "e2e4 e7e5 g1f3 b8c6",
                "1000",
                "100",
                "80",
                "100",
                "opening short",
                "https://lichess.org/example",
                "Kings_Pawn_Game",
            ],
            strict=True,
        )
    )
    row.update({key: str(value) for key, value in changes.items()})
    return row


def compressed_csv(rows: list[dict[str, str]]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    return zstandard.ZstdCompressor().compress(text.getvalue().encode())


def save_csv(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "puzzles.csv.zst"
    path.write_bytes(compressed_csv(rows))
    return path


@pytest.fixture
def database() -> Iterator[Engine]:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine, tables=[SQLModel.metadata.tables[name] for name in ("contentitem", "puzzletheme")]
    )
    yield engine
    engine.dispose()


@pytest.fixture
def session(database: Engine) -> Iterator[Session]:
    with Session(database) as session:
        yield session


def test_filters_boundaries_underfill_and_provenance(session: Session, tmp_path: Path) -> None:
    path = save_csv(
        tmp_path,
        [
            puzzle("low", Rating=600),
            puzzle("high", Rating=1800),
            puzzle("under", Rating=599),
            puzzle("over", Rating=1801),
            puzzle("rd", RatingDeviation=101),
            puzzle("pop", Popularity=79),
            puzzle("plays", NbPlays=99),
        ],
    )
    summary = import_puzzles(session, path)
    assert summary.read == 7
    assert summary.accepted == summary.inserted == 2
    assert summary.underfill
    assert summary.skipped == {"rating": 2, "rating_deviation": 1, "popularity": 1, "plays": 1}
    assert summary.provenance.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert summary.provenance.compressed_size == path.stat().st_size
    assert summary.provenance.url is None
    assert summary.provenance.last_modified is None
    assert summary.provenance.etag is None
    assert summary.to_dict()["filters"] == {
        "limit": 50_000,
        "min_rating": 600,
        "max_rating": 1800,
        "max_rating_deviation": 100,
        "min_popularity": 80,
        "min_plays": 100,
    }


def test_explicit_filter_overrides(session: Session, tmp_path: Path) -> None:
    path = save_csv(tmp_path, [puzzle(Rating=2100, RatingDeviation=120, Popularity=70, NbPlays=20)])
    summary = import_puzzles(
        session,
        path,
        filters=ImportFilters(
            limit=1,
            min_rating=2000,
            max_rating=2200,
            max_rating_deviation=120,
            min_popularity=70,
            min_plays=20,
        ),
    )
    assert summary.accepted == 1
    assert not summary.underfill


def test_setup_and_complete_remaining_line(session: Session, tmp_path: Path) -> None:
    path = save_csv(tmp_path, [puzzle()])
    import_puzzles(session, path)
    item = session.get(ContentItem, "lichess:abc12")
    assert item is not None
    assert item.domain == "puzzles"
    assert item.version == "0.2.0"
    assert item.licence == "CC0-1.0"
    assert item.skill_mapping == {}
    assert item.payload["fen"] == chess.STARTING_FEN
    assert item.payload["moves"] == ["e2e4", "e7e5", "g1f3", "b8c6"]
    assert item.payload["setup"] == "e2e4"
    assert item.payload["import_sequence"] == 1
    lesson = read_lesson(item)
    board = chess.Board()
    board.push_uci("e2e4")
    assert lesson.source.kind == "external"
    assert lesson.source.fen == board.fen()
    assert lesson.schema_version == "0.2.0"
    assert lesson.interaction.solution == ["e7e5", "g1f3", "b8c6"]
    assert lesson.verification.status == "source_verified"
    assert "diagnosis" not in item.payload["lesson"]
    assert "mastery" not in item.payload["lesson"]
    assert "engine" not in item.payload["lesson"]["verification"]
    assert item.payload["provenance"]["sha256"]
    assert item.payload["filters"]["limit"] == 50_000


def test_exact_underpromotion_survives_import(session: Session, tmp_path: Path) -> None:
    path = save_csv(
        tmp_path,
        [
            puzzle(
                FEN="7k/P7/8/8/8/8/8/4K3 b - - 0 1",
                Moves="h8h7 a7a8n",
            )
        ],
    )
    summary = import_puzzles(session, path)
    assert summary.accepted == 1
    item = session.get(ContentItem, "lichess:abc12")
    assert item is not None
    assert read_lesson(item).interaction.solution == ["a7a8n"]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"Moves": "e2e4"}, "invalid_parity"),
        ({"Moves": "e2e4 e7e5 g1f3"}, "invalid_parity"),
        ({"Moves": ""}, "invalid_parity"),
        ({"Moves": "e2e5 e7e5"}, "invalid_solution"),
        ({"Moves": "e2e4 e7e4"}, "invalid_solution"),
        ({"FEN": "not a fen"}, "invalid_solution"),
        ({"FEN": "8/8/8/8/8/8/8/8 w - - 0 1"}, "invalid_solution"),
        ({"FEN": "7k/P7/8/8/8/8/8/4K3 b - - 0 1", "Moves": "h8h7 a7a8"}, "invalid_solution"),
        ({"Rating": "unknown"}, "malformed_row"),
        ({"PuzzleId": ""}, "malformed_row"),
        ({"RatingDeviation": -1}, "malformed_row"),
    ],
)
def test_invalid_rows_are_reported(
    session: Session, tmp_path: Path, changes: dict[str, str | int], reason: str
) -> None:
    summary = import_puzzles(session, save_csv(tmp_path, [puzzle(**changes)]))
    assert summary.read == 1
    assert summary.accepted == 0
    assert summary.skipped == {reason: 1}
    assert summary.underfill


def test_header_order_and_malformed_columns(session: Session, tmp_path: Path) -> None:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=list(reversed(HEADERS)))
    writer.writeheader()
    writer.writerow(puzzle())
    text.write("missing,columns\n")
    writer.writerow(puzzle("second"))
    path = tmp_path / "header.csv.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress(text.getvalue().encode()))
    summary = import_puzzles(session, path)
    assert summary.accepted == 2
    assert summary.skipped == {"malformed_row": 1}


def test_bad_csv_quoting_is_reported(session: Session, tmp_path: Path) -> None:
    raw = ",".join(HEADERS) + '\n"unterminated'
    path = tmp_path / "malformed.csv.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress(raw.encode()))
    summary = import_puzzles(session, path)
    assert summary.skipped == {"malformed_row": 1}
    assert summary.read == 1


def test_missing_header_is_fatal_before_import(session: Session, tmp_path: Path) -> None:
    path = tmp_path / "missing.csv.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress(b"PuzzleId,FEN\na,b\n"))
    with pytest.raises(ValueError, match="header"):
        import_puzzles(session, path)
    assert session.exec(select(ContentItem)).all() == []


def test_duplicates_do_not_consume_cap_and_rerun_does_not_advance(
    session: Session, tmp_path: Path
) -> None:
    path = save_csv(tmp_path, [puzzle("one"), puzzle("one"), puzzle("two"), puzzle("three")])
    filters = ImportFilters(limit=2)
    first = import_puzzles(session, path, filters=filters)
    assert (first.read, first.accepted, first.inserted) == (3, 2, 2)
    assert first.skipped == {"duplicate": 1}
    second = import_puzzles(session, path, filters=filters)
    assert (second.read, second.accepted, second.inserted, second.existing) == (3, 2, 0, 2)
    assert second.metadata_changed == 0
    assert session.get(ContentItem, "lichess:three") is None
    assert len(session.exec(select(ContentItem)).all()) == 2


def test_metadata_refresh_preserves_identity_lesson_and_order(
    session: Session, tmp_path: Path
) -> None:
    path = save_csv(tmp_path, [puzzle()])
    import_puzzles(session, path)
    item = session.get(ContentItem, "lichess:abc12")
    assert item is not None
    before = {
        key: item.payload[key]
        for key in ("puzzle_id", "fen", "moves", "setup", "lesson", "import_sequence", "provenance")
    }
    save_csv(tmp_path, [puzzle(Rating=1200, Themes="fork middlegame", NbPlays=150)])
    summary = import_puzzles(session, path)
    session.refresh(item)
    assert (summary.accepted, summary.existing, summary.metadata_changed) == (1, 1, 1)
    assert item.payload["rating"] == 1200
    assert item.payload["themes"] == ["fork", "middlegame"]
    assert item.payload["metadata_provenance"]["sha256"] == summary.provenance.sha256
    assert {key: item.payload[key] for key in before} == before
    assert import_puzzles(session, path).metadata_changed == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"Moves": "e2e4 c7c5 g1f3 b8c6"},
        {"FEN": chess.STARTING_FEN.replace(" 0 1", " 1 2")},
    ],
)
def test_conflict_is_skipped_without_consuming_cap(
    session: Session, tmp_path: Path, changes: dict[str, str]
) -> None:
    import_puzzles(session, save_csv(tmp_path, [puzzle("one")]))
    path = save_csv(tmp_path, [puzzle("one", **changes), puzzle("two")])
    summary = import_puzzles(session, path, filters=ImportFilters(limit=1))
    assert summary.skipped == {"conflict": 1}
    assert summary.accepted == summary.inserted == 1
    original = session.get(ContentItem, "lichess:one")
    second = session.get(ContentItem, "lichess:two")
    assert original is not None and second is not None
    assert original.payload["fen"] == chess.STARTING_FEN
    assert original.payload["moves"][1] == "e7e5"
    assert second.payload["import_sequence"] == 2


def test_cap_across_database_batches(session: Session, tmp_path: Path) -> None:
    rows = [puzzle(f"p{index}") for index in range(270)]
    path = save_csv(tmp_path, rows)
    first = import_puzzles(session, path, filters=ImportFilters(limit=260))
    second = import_puzzles(session, path, filters=ImportFilters(limit=260))
    assert first.inserted == second.existing == 260
    assert second.inserted == 0
    assert second.read == 260
    assert session.get(ContentItem, "lichess:p260") is None


class Chunks(httpx.SyncByteStream):
    def __init__(self, content: bytes, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail

    def __iter__(self) -> Iterator[bytes]:
        midpoint = len(self.content) // 2
        yield self.content[:midpoint]
        if self.fail:
            raise httpx.ReadError("download interrupted")
        yield self.content[midpoint:]


def test_complete_atomic_download_provenance_and_local_import(
    session: Session, tmp_path: Path
) -> None:
    content = compressed_csv([puzzle()])
    output = tmp_path / "data" / "puzzles.csv.zst"
    output.parent.mkdir()
    output.write_bytes(b"previous file")

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DEFAULT_PUZZLE_URL
        return httpx.Response(
            200,
            stream=Chunks(content),
            headers={
                "Content-Length": str(len(content)),
                "ETag": '"dataset-v1"',
                "Last-Modified": "Thu, 12 Nov 2026 00:00:00 GMT",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provenance = download_puzzles(output, client=client)
    assert output.read_bytes() == content
    assert not list(output.parent.glob("*.part"))
    assert provenance.sha256 == hashlib.sha256(content).hexdigest()
    assert provenance.compressed_size == len(content)
    assert provenance.url == DEFAULT_PUZZLE_URL
    assert provenance.etag == '"dataset-v1"'
    assert provenance.last_modified == "Thu, 12 Nov 2026 00:00:00 GMT"
    assert provenance.origin == "download"
    assert "not upstream authentication" in provenance.integrity_note
    downloaded = import_puzzles(session, output, provenance=provenance)
    local = import_puzzles(session, output)
    assert downloaded.provenance == provenance
    assert downloaded.inserted == local.existing == 1
    assert local.inserted == local.metadata_changed == 0
    assert local.provenance.sha256 == provenance.sha256
    assert local.provenance.url is None


@pytest.mark.parametrize("failure", ["interrupted", "short", "http"])
def test_failed_download_preserves_saved_file(tmp_path: Path, failure: str) -> None:
    output = tmp_path / "puzzles.csv.zst"
    output.write_bytes(b"original")

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503 if failure == "http" else 200,
            stream=Chunks(b"replacement", fail=failure == "interrupted"),
            headers={"Content-Length": "100" if failure == "short" else "11"},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises((httpx.HTTPError, ValueError)):
            download_puzzles(output, client=client)
    assert output.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [output]


def test_cli_json_summary(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = save_csv(tmp_path, [puzzle()])
    monkeypatch.setattr(puzzles_cli, "engine", database)
    monkeypatch.setattr(puzzles_cli, "create_db_and_tables", lambda: None)
    monkeypatch.setattr("sys.argv", ["scan64-cli", "puzzles", "import", str(path), "--json"])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["accepted"] == summary["inserted"] == 1
    assert summary["metadata_changed"] == 0
    assert summary["underfill"] is True
    assert summary["skipped_total"] == 0
    assert summary["provenance"]["sha256"]
    assert summary["filters"]["limit"] == 50_000


def test_cli_text_underfill(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = save_csv(tmp_path, [puzzle()])
    monkeypatch.setattr(puzzles_cli, "engine", database)
    monkeypatch.setattr(puzzles_cli, "create_db_and_tables", lambda: None)
    monkeypatch.setattr("sys.argv", ["scan64-cli", "puzzles", "import", str(path)])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "Provenance:" in output and "Filters:" in output
    assert "metadata_changed 0" in output and "Skipped by reason:" in output
    assert "Underfill: 1 of 50000" in output and "not relaxed" in output


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["file.csv.zst", "--download"],
        ["--download", "--limit", "0"],
        ["file.csv.zst", "--output", "other.csv.zst"],
        ["file.csv.zst", "--url", "http://[x/a"],
    ],
)
def test_cli_invalid_inputs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["scan64-cli", "puzzles", "import", *arguments])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 1
    assert "Puzzle import failed" in capsys.readouterr().err


def test_cli_downloads_complete_file_before_import(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    content = compressed_csv([puzzle()])
    output = tmp_path / "custom.csv.zst"
    source_url = "https://example.test/puzzles.csv.zst"

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == source_url
        return httpx.Response(200, stream=Chunks(content), headers={"ETag": '"v2"'})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:

        def download(
            path: Path, *, url: str, progress: ProgressCallback | None
        ) -> SourceProvenance:
            assert path == output
            return download_puzzles(path, url=url, client=client, progress=progress)

        def initialized() -> None:
            # Database import cannot start until the complete download is saved.
            assert output.read_bytes() == content

        monkeypatch.setattr(puzzles_cli, "engine", database)
        monkeypatch.setattr(puzzles_cli, "create_db_and_tables", initialized)
        monkeypatch.setattr(puzzles_cli, "download_puzzles", download)
        monkeypatch.setattr(
            "sys.argv",
            [
                "scan64-cli",
                "puzzles",
                "import",
                "--download",
                "--url",
                source_url,
                "--data-dir",
                str(tmp_path),
                "--output",
                output.name,
                "--limit",
                "1",
                "--json",
            ],
        )
        with pytest.raises(SystemExit) as result:
            main()
    assert result.value.code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["accepted"] == summary["inserted"] == 1
    assert summary["underfill"] is False
    assert summary["provenance"]["url"] == source_url
    assert summary["provenance"]["etag"] == '"v2"'
    assert summary["provenance"]["last_modified"] is None
    assert summary["provenance"]["sha256"] == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    "filters",
    [
        {"limit": 0},
        {"min_rating": 1801, "max_rating": 1800},
        {"max_rating_deviation": -1},
        {"min_plays": -1},
    ],
)
def test_invalid_filter_configuration(filters: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ImportFilters(**filters)


@pytest.mark.parametrize("failure", ["cancel", "failure"])
def test_desktop_import_rolls_back_batches_and_metadata(
    database: Engine, tmp_path: Path, failure: str
) -> None:
    path = save_csv(tmp_path, [puzzle("original")])
    run_import(database, path, Event(), lambda _: None)
    path = save_csv(
        tmp_path,
        [
            puzzle("original", Rating=1200, Themes="fork pin"),
            *[puzzle(f"new{i}") for i in range(300)],
        ],
    )
    cancel = Event()

    def progress(message: str) -> None:
        if "batch 1" in message:
            if failure == "cancel":
                cancel.set()
            else:
                raise ValueError("simulated read failure after a committed batch")

    with pytest.raises(ImportCancelled if failure == "cancel" else ValueError):
        run_import(database, path, cancel, progress)
    with Session(database) as session:
        items = session.exec(select(ContentItem)).all()
        assert len(items) == 1
        assert items[0].payload["rating"] == 1000
        assert set(session.exec(select(PuzzleTheme.theme)).all()) == {"opening", "short"}


def test_desktop_import_success_and_precancel(database: Engine, tmp_path: Path) -> None:
    path = save_csv(tmp_path, [puzzle()])
    cancel = Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        run_import(database, path, cancel, lambda _: None)
    messages: list[str] = []
    summary = run_import(database, path, Event(), messages.append)
    assert summary.inserted == 1
    assert any("batch 1" in message for message in messages)
    with Session(database) as session:
        assert len(session.exec(select(ContentItem)).all()) == 1
        assert set(session.exec(select(PuzzleTheme.theme)).all()) == {"opening", "short"}


def test_app_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    from scan64.api.app import app_info

    monkeypatch.delenv("SCAN64_DISTRIBUTION", raising=False)
    assert app_info() == {"distribution": "source"}
    monkeypatch.setenv("SCAN64_DISTRIBUTION", "desktop")
    assert app_info() == {"distribution": "desktop"}


def test_desktop_staging_keeps_live_database_writable(tmp_path: Path) -> None:
    live = create_engine(f"sqlite:///{tmp_path / 'live.db'}", connect_args={"timeout": 0.1})
    SQLModel.metadata.create_all(
        live, tables=[SQLModel.metadata.tables[name] for name in ("contentitem", "puzzletheme")]
    )
    path = save_csv(tmp_path, [puzzle(f"row{i}") for i in range(300)])
    wrote = False

    def progress(message: str) -> None:
        nonlocal wrote
        if "batch 1" in message:
            with Session(live) as session:
                session.add(
                    ContentItem(id="concurrent", domain="opening", provenance="test", licence="CC0")
                )
                session.commit()
            wrote = True

    try:
        assert run_import(live, path, Event(), progress).inserted == 300
        assert wrote
        with Session(live) as session:
            assert session.get(ContentItem, "concurrent") is not None
    finally:
        live.dispose()


class TerminalStream(io.StringIO):
    def isatty(self) -> bool:
        return True


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def renders(output: str) -> list[str]:
    """Every state a terminal showed: carriage returns redraw, newlines end a phase."""
    return [
        part.removesuffix("\x1b[K")
        for line in output.split("\n")
        for part in line.split("\r")
        if part.removesuffix("\x1b[K")
    ]


def bulky_csv(count: int) -> bytes:
    # Random opening tags keep the compressed file large enough to show MB progress.
    return compressed_csv(
        [puzzle(f"p{i}", OpeningTags=secrets.token_hex(2000)) for i in range(count)]
    )


@pytest.mark.parametrize("with_length", [True, False])
def test_download_progress_is_monotonic_and_ends_at_size(tmp_path: Path, with_length: bool) -> None:
    content = compressed_csv([puzzle()])
    events: list[ImportProgress] = []

    def respond(request: httpx.Request) -> httpx.Response:
        headers = {"Content-Length": str(len(content))} if with_length else {}
        return httpx.Response(200, stream=Chunks(content), headers=headers)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        download_puzzles(tmp_path / "puzzles.csv.zst", client=client, progress=events.append)
    assert events[0] == ImportProgress("connect", 0, None, "bytes")
    downloads = events[1:]
    assert {event.phase for event in downloads} == {"download"}
    done = [event.done for event in downloads]
    assert done == sorted(done) and done[0] == 0 and len(set(done)) == 3
    assert downloads[-1].done == len(content)
    expected_total = len(content) if with_length else None
    assert {event.total for event in downloads} == {expected_total}


def test_interrupted_download_leaves_no_partial_file(tmp_path: Path) -> None:
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"first half"
            raise KeyboardInterrupt

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Interrupted())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(KeyboardInterrupt):
            download_puzzles(tmp_path / "puzzles.csv.zst", client=client)
    assert list(tmp_path.iterdir()) == []


def test_import_progress_counts_kept_puzzles_and_rows(session: Session, tmp_path: Path) -> None:
    path = save_csv(tmp_path, [puzzle(f"p{i}") for i in range(300)])
    events: list[ImportProgress] = []
    summary = import_puzzles(
        session, path, filters=ImportFilters(limit=280), progress=events.append
    )
    reads = [event for event in events if event.phase == "read"]
    imports = [event for event in events if event.phase == "import"]
    assert events == reads + imports
    assert reads[-1].done == reads[-1].total == path.stat().st_size
    assert imports[0] == ImportProgress("import", 0, 280, "puzzles", rows_read=0)
    assert [event.done for event in imports] == [0, 256, 280]
    assert imports[-1].done == summary.accepted
    assert imports[-1].rows_read == summary.read


def test_piped_progress_writes_plain_lines_at_most_every_five_seconds() -> None:
    stream = io.StringIO()
    clock = FakeClock()
    render = puzzles_cli.ProgressRenderer(stream, source="database.lichess.org", clock=clock)
    for at, event in [
        (0.0, ImportProgress("connect", 0, None, "bytes")),
        (0.5, ImportProgress("download", 0, 100_000_000, "bytes")),
        (2.0, ImportProgress("download", 10_000_000, 100_000_000, "bytes")),
        (5.2, ImportProgress("download", 50_000_000, 100_000_000, "bytes")),
        (7.0, ImportProgress("download", 60_000_000, 100_000_000, "bytes")),
        (10.3, ImportProgress("import", 1_000, 50_000, "puzzles", rows_read=4_000)),
    ]:
        clock.now = at
        render(event)
    render.close()
    assert "\r" not in stream.getvalue()
    assert stream.getvalue().splitlines() == [
        "Connecting to database.lichess.org…",
        "Downloading  50.0 / 100.0 MB  50%  10.6 MB/s  0:05 left",
        "Importing    1,000 / 50,000 puzzles kept  ·  4,000 rows read",
    ]


def test_terminal_progress_redraws_one_line_per_phase() -> None:
    stream = TerminalStream()
    clock = FakeClock()
    render = puzzles_cli.ProgressRenderer(stream, source="database.lichess.org", clock=clock)
    for at, event in [
        (0.0, ImportProgress("connect", 0, None, "bytes")),
        (0.2, ImportProgress("download", 0, None, "bytes")),
        (1.2, ImportProgress("download", 2_000_000, None, "bytes")),
        (1.25, ImportProgress("download", 2_500_000, None, "bytes")),
        (2.0, ImportProgress("import", 0, 50_000, "puzzles", rows_read=0)),
    ]:
        clock.now = at
        render(event)
    render.close()
    assert stream.getvalue().count("\n") == 3
    assert renders(stream.getvalue()) == [
        "Connecting to database.lichess.org…",
        "Downloading  0.0 MB",
        "Downloading  2.0 MB  2.0 MB/s",
        # Throttled within 0.1 s, then drawn as of its arrival when the phase ends.
        "Downloading  2.5 MB  2.4 MB/s",
        "Importing    0 / 50,000 puzzles kept  ·  0 rows read",
    ]


@contextmanager
def serve_slowly(content: bytes, *, with_length: bool) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            if with_length:
                self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            step = len(content) // 6 + 1
            for start in range(0, len(content), step):
                self.wfile.write(content[start : start + step])
                self.wfile.flush()
                time.sleep(0.15)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/lichess_db_puzzle.csv.zst"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("with_length", [True, False])
def test_cli_download_shows_progress_on_stderr_and_one_json_document(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    with_length: bool,
) -> None:
    content = bulky_csv(300)
    terminal = TerminalStream()
    monkeypatch.setattr(puzzles_cli, "engine", database)
    monkeypatch.setattr(puzzles_cli, "create_db_and_tables", lambda: None)
    with serve_slowly(content, with_length=with_length) as url:
        monkeypatch.setattr(
            "sys.argv",
            ["scan64-cli", "puzzles", "import", "--download", "--url", url]
            + ["--data-dir", str(tmp_path), "--limit", "100", "--json"],
        )
        monkeypatch.setattr("sys.stderr", terminal)
        with pytest.raises(SystemExit) as result:
            main()
    assert result.value.code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["accepted"] == 100
    shown = renders(terminal.getvalue())
    assert shown[0] == "Connecting to 127.0.0.1…"
    downloads = [line for line in shown if line.startswith("Downloading")]
    assert len(set(downloads)) >= 2
    assert any("MB/s" in line for line in downloads)
    if with_length:
        assert all("%" in line for line in downloads)
        assert any(line.endswith("left") for line in downloads)
    else:
        assert not any("%" in line or "left" in line for line in downloads)
    assert shown[-1] == "Importing    100 / 100 puzzles kept  ·  100 rows read"
    assert not list(tmp_path.glob("*.part"))


def test_cli_quiet_writes_no_progress(
    database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = save_csv(tmp_path, [puzzle()])
    monkeypatch.setattr(puzzles_cli, "engine", database)
    monkeypatch.setattr(puzzles_cli, "create_db_and_tables", lambda: None)
    monkeypatch.setattr("sys.argv", ["scan64-cli", "puzzles", "import", str(path), "--quiet"])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "accepted 1 unique rows" in captured.out


@pytest.mark.parametrize("desktop", [False, True])
def test_all_catalogue_writers_replace_themes_on_metadata_refresh(
    database: Engine, tmp_path: Path, desktop: bool
) -> None:
    def write(rows: list[dict[str, str]]) -> None:
        path = save_csv(tmp_path, rows)
        if desktop:
            run_import(database, path, Event(), lambda _: None)
        else:
            with Session(database) as session:
                import_puzzles(session, path)

    def assert_projection() -> None:
        with Session(database) as session:
            expected = {
                (item.id, theme)
                for item in session.exec(select(ContentItem))
                for theme in item.payload["themes"]
            }
            assert set(session.exec(select(PuzzleTheme.item_id, PuzzleTheme.theme))) == expected

    write([puzzle("one"), puzzle("two", Themes="fork fork pin")])
    assert_projection()
    write([puzzle("one", Themes="mateIn1"), puzzle("two", Themes="")])
    assert_projection()
    # Idempotent imports do not duplicate the projection or change identity.
    write([puzzle("one", Themes="mateIn1"), puzzle("two", Themes="")])
    assert_projection()


def test_cancel_after_live_theme_merge_rolls_back_catalogue_and_themes(
    database: Engine, tmp_path: Path
) -> None:
    path = save_csv(tmp_path, [puzzle("original")])
    run_import(database, path, Event(), lambda _: None)
    path = save_csv(tmp_path, [puzzle("original", Themes="fork"), puzzle("new")])
    cancel = Event()

    def before_commit(summary) -> None:
        cancel.set()

    with pytest.raises(ImportCancelled):
        run_import(database, path, cancel, lambda _: None, before_commit=before_commit)
    with Session(database) as session:
        assert [item.id for item in session.exec(select(ContentItem))] == ["lichess:original"]
        assert set(session.exec(select(PuzzleTheme.item_id, PuzzleTheme.theme))) == {
            ("lichess:original", "opening"),
            ("lichess:original", "short"),
        }
