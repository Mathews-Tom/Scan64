from __future__ import annotations

import runpy
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import zstandard
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine, select

import scan64.puzzle_import_job as job_module
from scan64.api.desktop_security import add_desktop_security
from scan64.api.puzzle_import import router
from scan64.content.models import ContentItem
from scan64.content.puzzle_import import ImportProgress, ImportSummary, import_puzzles
from scan64.puzzle_import_job import (
    ImportJobState,
    JobAlreadyRunning,
    PuzzleImportJob,
    validate_puzzle_path,
)

TOKEN = "import-test-shell-token"
CSV = (
    "PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes\n"
    "fixture,rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1,"
    "e2e4 e7e5 g1f3 b8c6,1000,80,90,200,opening short\n"
)
PAYLOAD = zstandard.ZstdCompressor().compress(CSV.encode())


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    database = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    SQLModel.metadata.create_all(database, tables=[SQLModel.metadata.tables["contentitem"]])
    yield database
    database.dispose()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "local.csv.zst"
    path.write_bytes(PAYLOAD)
    return path


def catalog(engine: Engine) -> list[tuple[str, dict[str, object]]]:
    with Session(engine) as session:
        return [(item.id, item.payload) for item in session.exec(select(ContentItem)).all()]


def finished(job: PuzzleImportJob) -> ImportJobState:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = job.state()
        if state.status not in {"running", "cancelling"}:
            return state
        time.sleep(0.005)
    pytest.fail("Import did not finish promptly")


@contextmanager
def fixture_server(*, stall: bool = False, invalid: bool = False) -> Iterator[str]:
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            data = b"not a zstd file" if invalid else PAYLOAD
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                if stall:
                    self.wfile.write(data[:1])
                    self.wfile.flush()
                    release.wait(10)
                    self.wfile.write(data[1:])
                else:
                    self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/sample.csv.zst"
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


class ObservedJob(PuzzleImportJob):
    def __init__(self, engine: Engine, data_dir: Path, **kwargs: str) -> None:
        super().__init__(engine, data_dir, **kwargs)
        self.phases: list[str] = []
        self.amounts: list[int] = []
        self.pause: str | None = None
        self.reached = threading.Event()
        self.release = threading.Event()

    def _progress(self, update: ImportProgress) -> None:
        super()._progress(update)
        phase = self.state().phase
        assert phase is not None
        self.phases.append(phase)
        self.amounts.append(update.done)
        if phase == self.pause:
            self.reached.set()
            assert self.release.wait(5)
        # A cancel while the callback is paused must still stop before any writes.
        super()._progress(update)

    def _message(self, message: str) -> None:
        super()._message(message)
        if message.startswith("Saving"):
            self.phases.append("save")
            if self.pause == "save":
                self.reached.set()
                assert self.release.wait(5)
                super()._message(message)


def test_download_success_phases_progress_cleanup_and_default_filters(
    engine: Engine, tmp_path: Path
) -> None:
    with fixture_server() as url:
        job = ObservedJob(engine, tmp_path / "imports", download_url=url)
        assert job.state().status == "idle"
        assert job.start("download").status == "running"
        result = finished(job)
    assert result.status == "succeeded", result.error
    assert result.summary is not None and result.summary.inserted == 1
    assert list(dict.fromkeys(job.phases)) == ["download", "read", "import", "save"]
    assert max(job.amounts) > 0
    assert list((tmp_path / "imports").iterdir()) == []
    rows = catalog(engine)
    assert len(rows) == 1
    assert rows[0][1]["rating"] == 1000
    assert rows[0][1]["filters"]["limit"] == 50_000
    assert rows[0][1]["provenance"]["origin"] == "download"


def test_local_import_keeps_user_file(engine: Engine, source: Path, tmp_path: Path) -> None:
    job = PuzzleImportJob(engine, tmp_path / "imports")
    job.start("file", str(source))
    assert finished(job).status == "succeeded"
    assert source.read_bytes() == PAYLOAD
    assert len(catalog(engine)) == 1


@pytest.mark.parametrize("phase", ["download", "read", "import", "save"])
def test_cancel_each_phase_preserves_catalog_and_cleans_files(
    engine: Engine, source: Path, tmp_path: Path, phase: str
) -> None:
    with Session(engine) as session:
        import_puzzles(session, source)
    original = catalog(engine)
    with fixture_server() as url:
        job = ObservedJob(engine, tmp_path / "imports", download_url=url)
        job.pause = phase
        job.start("download")
        try:
            assert job.reached.wait(5)
            assert job.cancel().status == "cancelling"
        finally:
            job.release.set()
        assert finished(job).status == "cancelled"
    assert catalog(engine) == original
    assert list((tmp_path / "imports").iterdir()) == []
    assert source.is_file()


def test_cancel_interrupts_stalled_http_stream(engine: Engine, tmp_path: Path) -> None:
    with fixture_server(stall=True) as url:
        job = ObservedJob(engine, tmp_path / "imports", download_url=url)
        job.start("download")
        deadline = time.monotonic() + 3
        while job.state().done == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert job.state().done == 1
        started = time.monotonic()
        job.cancel()
        assert finished(job).status == "cancelled"
        assert time.monotonic() - started < 1
    assert not catalog(engine)
    assert list((tmp_path / "imports").iterdir()) == []


def test_single_job_rule_and_restart_after_cancel(
    engine: Engine, source: Path, tmp_path: Path
) -> None:
    job = ObservedJob(engine, tmp_path / "imports")
    job.pause = "read"
    job.start("file", str(source))
    try:
        assert job.reached.wait(5)
        with pytest.raises(JobAlreadyRunning):
            job.start("download")
        job.cancel()
        with pytest.raises(JobAlreadyRunning):
            job.start("file", str(source))
    finally:
        job.release.set()
    assert finished(job).status == "cancelled"
    job.pause = None
    job.start("file", str(source))
    assert finished(job).status == "succeeded"
    assert job.cancel().status == "succeeded"


def test_bad_download_fails_without_catalog_or_download_file(
    engine: Engine, tmp_path: Path
) -> None:
    with fixture_server(invalid=True) as url:
        job = PuzzleImportJob(engine, tmp_path / "imports", download_url=url)
        job.start("download")
        state = finished(job)
    assert state.status == "failed" and state.error
    assert url not in state.error
    assert not catalog(engine)
    assert list((tmp_path / "imports").iterdir()) == []


@pytest.mark.parametrize("phase", ["download", "read", "import", "save"])
def test_failure_each_phase_preserves_catalog(
    engine: Engine, source: Path, tmp_path: Path, phase: str
) -> None:
    with Session(engine) as session:
        import_puzzles(session, source)
    original = catalog(engine)

    class FailingJob(PuzzleImportJob):
        def _progress(self, update: ImportProgress) -> None:
            super()._progress(update)
            if self.state().phase == phase:
                raise ValueError("fixture failure")

        def _message(self, message: str) -> None:
            super()._message(message)
            if phase == "save" and message.startswith("Saving"):
                raise ValueError("fixture save failure")

    with fixture_server() as url:
        job = FailingJob(engine, tmp_path / "imports", download_url=url)
        job.start("download")
        assert finished(job).status == "failed"
    assert catalog(engine) == original
    assert list((tmp_path / "imports").iterdir()) == []


def test_cancel_after_commit_reports_success_not_cancelled(
    engine: Engine, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed = threading.Event()
    release = threading.Event()
    original_import = job_module.run_import

    def delayed_import(*args: object, **kwargs: object) -> ImportSummary:
        result = original_import(*args, **kwargs)
        committed.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(job_module, "run_import", delayed_import)
    job = PuzzleImportJob(engine, tmp_path / "imports")
    job.start("file", str(source))
    try:
        assert committed.wait(5)
        assert job.cancel().status == "running"
    finally:
        release.set()
    assert finished(job).status == "succeeded"
    assert len(catalog(engine)) == 1


@pytest.mark.parametrize("name", ["missing.csv.zst", "wrong.csv", "directory.csv.zst"])
def test_reject_invalid_local_paths(tmp_path: Path, name: str) -> None:
    (tmp_path / "wrong.csv").write_text("no")
    (tmp_path / "directory.csv.zst").mkdir()
    with pytest.raises(ValueError, match="readable regular"):
        validate_puzzle_path(str(tmp_path / name))
    with pytest.raises(ValueError):
        validate_puzzle_path("relative.csv.zst")


def app_for(job: PuzzleImportJob) -> FastAPI:
    app = FastAPI()
    app.state.puzzle_import_job = job
    app.include_router(router)
    add_desktop_security(app, TOKEN)
    return app


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("token", [None, "wrong", TOKEN])
def test_endpoints_require_shell_token(
    engine: Engine, tmp_path: Path, method: str, token: str | None
) -> None:
    job = PuzzleImportJob(engine, tmp_path / "imports")
    client = TestClient(app_for(job))
    headers = {} if token is None else {"X-Scan64-Shell-Token": token}
    response = client.request(
        method, "/v1/app/puzzle-import", headers=headers, json={"source": "file"}
    )
    expected = (422 if method == "POST" else 200) if token == TOKEN else 401
    assert response.status_code == expected
    assert job.state().status == "idle"


def test_tokened_endpoints_start_progress_conflict_cancel(
    engine: Engine, tmp_path: Path, source: Path
) -> None:
    job = ObservedJob(engine, tmp_path / "imports")
    job.pause = "read"
    client = TestClient(app_for(job), headers={"X-Scan64-Shell-Token": TOKEN})
    assert (
        client.post(
            "/v1/app/puzzle-import", json={"source": "file", "path": str(source)}
        ).status_code
        == 202
    )
    try:
        assert job.reached.wait(5)
        assert client.get("/v1/app/puzzle-import").json()["phase"] == "read"
        assert client.post("/v1/app/puzzle-import", json={"source": "download"}).status_code == 409
        assert client.delete("/v1/app/puzzle-import").json()["status"] == "cancelling"
    finally:
        job.release.set()
    assert finished(job).status == "cancelled"


@pytest.mark.parametrize(
    "body",
    [
        {"source": "file"},
        {"source": "download", "path": "/tmp/file.csv.zst"},
        {"source": "download", "url": "http://localhost"},
        {"source": "file", "path": "/tmp/no.csv"},
    ],
)
def test_start_rejects_invalid_input(engine: Engine, tmp_path: Path, body: dict[str, str]) -> None:
    job = PuzzleImportJob(engine, tmp_path / "imports")
    client = TestClient(app_for(job), headers={"X-Scan64-Shell-Token": TOKEN})
    assert client.post("/v1/app/puzzle-import", json=body).status_code == 422
    assert job.state().status == "idle"


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_browser_install_returns_404(method: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAN64_SHELL_TOKEN", raising=False)
    monkeypatch.setenv("SCAN64_WEB_DIST", "/nonexistent-test-client")
    namespace = runpy.run_module("scan64.api.app")
    client = TestClient(namespace["app"])
    assert client.request(method, "/v1/app/puzzle-import", json={}).status_code == 404
