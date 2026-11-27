from __future__ import annotations

import asyncio
import json
import runpy
import threading
import time
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import httpx
import pytest
import zstandard
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, event
from sqlmodel import Session, SQLModel, create_engine, select
from starlette.types import Message, Scope

import scan64.puzzle_import_job as job_module
from scan64.api.desktop_security import add_desktop_security
from scan64.api.puzzle_import import import_events, router
from scan64.content.models import ContentItem
from scan64.content.puzzle_import import ImportProgress, ImportSummary, import_puzzles
from scan64.puzzle_import_job import (
    ImportJobState,
    JobAlreadyRunning,
    PuzzleImportJob,
    validate_puzzle_path,
)
from tests.streaming import disconnect_stream

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
    SQLModel.metadata.create_all(
        database, tables=[SQLModel.metadata.tables[name] for name in ("contentitem", "puzzletheme")]
    )
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


def test_post_commit_exception_preserves_success_and_summary(
    engine: Engine, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_import = job_module.run_import

    def cleanup_failure(*args: object, **kwargs: object) -> ImportSummary:
        original_import(*args, **kwargs)
        raise OSError("fixture cleanup failed after commit")

    monkeypatch.setattr(job_module, "run_import", cleanup_failure)
    job = PuzzleImportJob(engine, tmp_path / "imports")
    job.start("file", str(source))
    state = finished(job)
    assert state.status == "succeeded"
    assert state.summary is not None and state.summary.inserted == 1
    assert state.error is not None and "Puzzles were imported" in state.error
    assert "unchanged" not in state.error
    assert len(catalog(engine)) == 1
    assert source.read_bytes() == PAYLOAD


def test_constructor_removes_only_abandoned_download_directories(
    engine: Engine, source: Path, tmp_path: Path
) -> None:
    data = tmp_path / "imports"
    stale = data / "puzzle-import-crashed"
    stale.mkdir(parents=True)
    (stale / ".puzzles.csv.zst.part").write_bytes(b"partial")
    keep = data / "other-directory"
    keep.mkdir()
    (keep / "private.txt").write_text("keep")
    regular = data / "puzzle-import-not-a-directory"
    regular.write_text("keep this regular file")
    PuzzleImportJob(engine, data)
    assert not stale.exists()
    assert (keep / "private.txt").read_text() == "keep"
    assert regular.read_text() == "keep this regular file"
    assert source.read_bytes() == PAYLOAD


def test_constructor_never_follows_stale_download_symlink(engine: Engine, tmp_path: Path) -> None:
    data = tmp_path / "imports"
    data.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep.txt").write_text("keep")
    link = data / "puzzle-import-link"
    link.symlink_to(external, target_is_directory=True)
    PuzzleImportJob(engine, data)
    assert not link.is_symlink()
    assert (external / "keep.txt").read_text() == "keep"


def test_unremovable_stale_entry_does_not_block_startup_or_other_cleanup(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "imports"
    blocked = data / "puzzle-import-blocked"
    blocked.mkdir(parents=True)
    (blocked / ".puzzles.csv.zst.part").write_bytes(b"held by another process")
    removable = data / "puzzle-import-removable"
    removable.mkdir()
    original_remove = job_module.shutil.rmtree

    def remove(path: Path) -> None:
        if path == blocked:
            raise PermissionError("simulated Windows sharing violation")
        original_remove(path)

    monkeypatch.setattr(job_module.shutil, "rmtree", remove)
    job = PuzzleImportJob(engine, data)
    assert job.state().status == "idle"
    assert blocked.is_dir()
    assert not removable.exists()


async def stream_for(job: PuzzleImportJob) -> AsyncGenerator[str, None]:
    request = Request({"type": "http", "app": app_for(job), "headers": []})
    response = await import_events(request)
    assert response.media_type == "application/x-ndjson"
    return cast(AsyncGenerator[str, None], response.body_iterator)


async def next_snapshot(body: AsyncGenerator[str, None]) -> ImportJobState:
    return ImportJobState.model_validate_json(await asyncio.wait_for(anext(body), 5))


async def terminal_snapshot(
    body: AsyncGenerator[str, None], previous: ImportJobState
) -> ImportJobState:
    while previous.status in {"running", "cancelling"}:
        current = await next_snapshot(body)
        assert current.revision > previous.revision
        previous = current
    with pytest.raises(StopAsyncIteration):
        await anext(body)
    return previous


@pytest.mark.parametrize("token", [None, "wrong", TOKEN])
def test_event_endpoint_shell_boundary_and_idle_snapshot(
    engine: Engine, tmp_path: Path, token: str | None
) -> None:
    job = PuzzleImportJob(engine, tmp_path / "imports")
    headers = {} if token is None else {"X-Scan64-Shell-Token": token}
    with TestClient(app_for(job)) as client:
        response = client.get("/v1/app/puzzle-import/events", headers=headers)
    assert response.status_code == (200 if token == TOKEN else 401)
    if token == TOKEN:
        assert response.headers["content-type"] == "application/x-ndjson"
        states = [json.loads(line) for line in response.text.splitlines()]
        assert states == [job.state().model_dump()]
        assert states[0]["revision"] == 0
    assert not job._listeners


@pytest.mark.asyncio
async def test_import_stream_capacity_is_reclaimed_on_disconnect(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(job_module, "_MAX_SUBSCRIPTIONS", 1)
    job = PuzzleImportJob(engine, tmp_path / "imports")
    body = await stream_for(job)
    try:
        assert (await next_snapshot(body)).status == "idle"
        with pytest.raises(HTTPException) as limit:
            await stream_for(job)
        assert limit.value.status_code == 429
    finally:
        await body.aclose()
    replacement = await stream_for(job)
    try:
        assert (await next_snapshot(replacement)).status == "idle"
    finally:
        await replacement.aclose()
        job.close()


@pytest.mark.asyncio
async def test_real_import_pushes_progress_committed_success_and_later_revision(
    engine: Engine, source: Path, tmp_path: Path
) -> None:
    job = ObservedJob(engine, tmp_path / "imports")
    previous_revision = 0
    try:
        for _ in range(2):
            job.pause = "read"
            job.reached.clear()
            job.release.clear()
            started = job.start("file", str(source))
            assert started.revision > previous_revision
            assert await asyncio.to_thread(job.reached.wait, 5)
            body = await stream_for(job)
            initial = await next_snapshot(body)
            assert initial.status == "running" and initial.phase == "read"
            assert initial.revision >= started.revision
            job.release.set()
            terminal = await terminal_snapshot(body, initial)
            assert terminal.status == "succeeded", terminal.error
            assert terminal.summary is not None
            assert len(catalog(engine)) == 1
            assert terminal.revision > initial.revision
            previous_revision = terminal.revision
            # A subscriber arriving after completion closes without waiting.
            late = await stream_for(job)
            assert await next_snapshot(late) == terminal
            with pytest.raises(StopAsyncIteration):
                await anext(late)
            assert not job._listeners
    finally:
        job.release.set()
        job.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["download", "read", "import", "save"])
async def test_real_cancellation_pushes_cancelling_then_cancelled(
    engine: Engine, tmp_path: Path, phase: str
) -> None:
    with fixture_server() as url:
        job = ObservedJob(engine, tmp_path / "imports", download_url=url)
        job.pause = phase
        job.start("download")
        assert await asyncio.to_thread(job.reached.wait, 5)
        body = await stream_for(job)
        try:
            initial = await next_snapshot(body)
            cancelled = job.cancel()
            pushed = await next_snapshot(body)
            assert pushed.status == "cancelling"
            assert pushed.revision == cancelled.revision > initial.revision
            job.release.set()
            terminal = await terminal_snapshot(body, pushed)
            assert terminal.status == "cancelled"
            assert not catalog(engine)
        finally:
            job.release.set()
            await body.aclose()
            job.close()
    assert not job._listeners


@pytest.mark.asyncio
async def test_real_invalid_import_pushes_sanitized_failure(
    engine: Engine, source: Path, tmp_path: Path
) -> None:
    source.write_bytes(b"not compressed puzzles")
    job = ObservedJob(engine, tmp_path / "imports")
    job.pause = "read"
    job.start("file", str(source))
    assert await asyncio.to_thread(job.reached.wait, 5)
    body = await stream_for(job)
    try:
        initial = await next_snapshot(body)
        job.release.set()
        terminal = await terminal_snapshot(body, initial)
        assert terminal.status == "failed" and terminal.error
        assert str(source) not in terminal.error
        assert not catalog(engine)
    finally:
        job.release.set()
        await body.aclose()
        job.close()
    assert not job._listeners


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_failure", [False, True])
async def test_push_success_only_after_commit_even_with_late_cancel_or_cleanup_error(
    engine: Engine,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_failure: bool,
) -> None:
    committed = threading.Event()
    release = threading.Event()
    original = job_module.run_import

    def delayed_import(*args: object, **kwargs: object) -> ImportSummary:
        result = original(*args, **kwargs)
        committed.set()
        assert release.wait(5)
        if cleanup_failure:
            raise OSError("private cleanup path must not escape")
        return result

    monkeypatch.setattr(job_module, "run_import", delayed_import)
    job = PuzzleImportJob(engine, tmp_path / "imports")
    job.start("file", str(source))
    assert await asyncio.to_thread(committed.wait, 5)
    body = await stream_for(job)
    try:
        initial = await next_snapshot(body)
        assert initial.status == "running"
        assert len(catalog(engine)) == 1
        assert job.cancel() == initial
        release.set()
        terminal = await terminal_snapshot(body, initial)
        assert terminal.status == "succeeded"
        assert terminal.summary is not None and terminal.summary.inserted == 1
        assert bool(terminal.error) == cleanup_failure
    finally:
        release.set()
        await body.aclose()
        job.close()
    assert not job._listeners


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["subscribe", "advance", "disconnect"])
async def test_asgi_stream_and_health_remain_responsive_during_actual_commit(
    engine: Engine, source: Path, tmp_path: Path, operation: str
) -> None:
    from scan64.api.app import health

    job = ObservedJob(engine, tmp_path / "imports")
    job.pause = "read"
    app = app_for(job)
    app.add_api_route("/health", health)
    commit_entered = threading.Event()
    release_commit = threading.Event()
    commit_timed_out = threading.Event()

    def pause_commit(_connection: Connection) -> None:
        # This engine's first commit snapshots the catalog; only its final live
        # merge has a recorded summary and still holds the cancellation lock.
        if job._result is not None and not job._committed.is_set():
            commit_entered.set()
            # An independent thread releases a broken implementation too: an
            # asyncio timeout alone cannot recover a blocked event loop.
            if not release_commit.wait(5):
                commit_timed_out.set()

    event.listen(engine, "commit", pause_commit)
    snapshots: asyncio.Queue[ImportJobState] = asyncio.Queue()
    disconnected = asyncio.Event()
    advance_stream = asyncio.Event()
    requested = False
    first_body = True

    async def receive() -> Message:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal first_body
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        elif message["type"] == "http.response.body" and message.get("body"):
            snapshots.put_nowait(ImportJobState.model_validate_json(message["body"]))
            if first_body:
                first_body = False
                # Hold the first write in flight so consuming a pending producer
                # wakeup and disconnect cleanup can each run inside the commit.
                await advance_stream.wait()

    path = "/v1/app/puzzle-import/events"
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "scheme": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"x-scan64-shell-token", TOKEN.encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    stream: asyncio.Task[None] | None = None
    try:
        job.start("file", str(source))
        assert await asyncio.to_thread(job.reached.wait, 5)
        initial = None
        if operation != "subscribe":
            stream = asyncio.create_task(app(scope, receive, send))
            initial = await asyncio.wait_for(snapshots.get(), 5)
            assert initial.status == "running" and initial.phase == "read"

        job.release.set()
        assert await asyncio.to_thread(commit_entered.wait, 5)
        assert job._lock.locked() and not job._committed.is_set()
        if operation == "subscribe":
            stream = asyncio.create_task(app(scope, receive, send))
            current = await asyncio.wait_for(snapshots.get(), 5)
            assert current.status == "running" and current.phase == "save"
        elif operation == "advance":
            advance_stream.set()
            current = await asyncio.wait_for(snapshots.get(), 5)
            assert initial is not None and current.revision > initial.revision
            assert current.status == "running" and current.phase == "save"
        else:
            disconnected.set()
            assert stream is not None
            await asyncio.wait_for(stream, 5)
            assert not job._listeners

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await asyncio.wait_for(client.get("/health"), 5)
        assert response.status_code == 200 and response.json() == {"status": "ok"}
        assert not commit_timed_out.is_set()
        assert job._lock.locked() and not job._committed.is_set()
        disconnected.set()
        assert stream is not None
        await asyncio.wait_for(stream, 5)
        assert not job._listeners
        assert not commit_timed_out.is_set() and not job._committed.is_set()
    finally:
        release_commit.set()
        job.release.set()
        disconnected.set()
        advance_stream.set()
        if stream is not None:
            await asyncio.wait_for(stream, 5)
        await asyncio.to_thread(job.close)
        event.remove(engine, "commit", pause_commit)
    result = job.state()
    assert result.status == "succeeded" and result.summary is not None
    assert result.summary.inserted == 1 and len(catalog(engine)) == 1


@pytest.mark.asyncio
async def test_atomic_subscription_and_coalesced_thread_progress(
    engine: Engine, tmp_path: Path
) -> None:
    job = PuzzleImportJob(engine, tmp_path / "imports")
    initial, listener = job.subscribe()
    assert initial.revision == 0
    initial.done = 999  # A subscriber cannot mutate the authoritative snapshot.
    another, other_listener = job.subscribe()
    assert another.done == 0 and another.revision == 0
    other_listener.close()
    try:

        def burst() -> None:
            for done in range(1000):
                job._progress(ImportProgress(phase="read", done=done, total=1000, unit="puzzles"))

        await asyncio.to_thread(burst)
        current = await asyncio.wait_for(listener.next_state(), 5)
        assert current is not None
        assert current.done == 999 and current.revision == 1000
        assert not listener._pending and not listener._event.is_set()
        # Completion after registration but before waiting must remain visible.
        with job._lock:
            job._state.status = "failed"
            job._state.error = "fixture failure"
            job._publish_locked()
        final = await asyncio.wait_for(listener.next_state(), 5)
        assert final is not None and final.status == "failed"
        assert final.revision > current.revision
    finally:
        listener.close()
        listener.close()
        job.close()
    assert not job._listeners


@pytest.mark.asyncio
async def test_stream_disconnect_cancellation_and_shutdown_release_subscribers(
    engine: Engine, source: Path, tmp_path: Path
) -> None:
    job = ObservedJob(engine, tmp_path / "imports")
    job.pause = "read"
    job.start("file", str(source))
    assert await asyncio.to_thread(job.reached.wait, 5)
    try:
        body = await stream_for(job)
        await next_snapshot(body)
        await body.aclose()
        assert not job._listeners
        body = await stream_for(job)
        await next_snapshot(body)
        waiting = asyncio.create_task(anext(body))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not job._listeners
        _, listener = job.subscribe()
        job.release.set()
        await asyncio.to_thread(job.close)
        assert await asyncio.wait_for(listener.next_state(), 5) is None
        assert not job._listeners
    finally:
        job.release.set()
        job.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("before_body", [False, True])
async def test_asgi_disconnect_releases_import_listener_before_or_during_body(
    engine: Engine, source: Path, tmp_path: Path, before_body: bool
) -> None:
    job = ObservedJob(engine, tmp_path / "imports")
    job.pause = "read"
    job.start("file", str(source))
    assert await asyncio.to_thread(job.reached.wait, 5)
    try:
        messages = await disconnect_stream(
            app_for(job),
            "/v1/app/puzzle-import/events",
            {"X-Scan64-Shell-Token": TOKEN},
            before_body=before_body,
        )
        assert messages[0]["status"] == 200
        if not before_body:
            assert json.loads(messages[1]["body"])["status"] == "running"
        assert not job._listeners
    finally:
        job.release.set()
        job.close()


@pytest.mark.asyncio
async def test_real_terminal_commit_between_subscription_and_first_body_is_not_lost(
    engine: Engine, source: Path, tmp_path: Path
) -> None:
    job = ObservedJob(engine, tmp_path / "imports")
    job.pause = "save"
    job.start("file", str(source))
    assert await asyncio.to_thread(job.reached.wait, 5)
    body = await stream_for(job)
    try:
        job.release.set()
        thread = job._thread
        assert thread is not None
        await asyncio.to_thread(thread.join, 5)
        assert not thread.is_alive()
        initial = await next_snapshot(body)
        assert initial.status == "running"
        terminal = await terminal_snapshot(body, initial)
        assert terminal.status == "succeeded"
        assert len(catalog(engine)) == 1
    finally:
        job.release.set()
        await body.aclose()
        job.close()
    assert not job._listeners


@pytest.mark.asyncio
async def test_thread_start_failure_publishes_failure_and_closes_cleanly(
    engine: Engine, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = PuzzleImportJob(engine, tmp_path / "imports")
    _, listener = job.subscribe()

    def fail_start(thread: threading.Thread) -> None:
        raise RuntimeError("private thread startup failure")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    try:
        with pytest.raises(RuntimeError, match="thread startup failure"):
            job.start("file", str(source))
        failed = await asyncio.wait_for(listener.next_state(), 5)
        assert failed is not None and failed.status == "failed" and failed.revision == 2
        assert failed.error is not None and "private" not in failed.error
    finally:
        listener.close()
        job.close()
    assert not job._listeners
