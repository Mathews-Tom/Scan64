"""One cancellable, server-owned puzzle import; never writes the live catalog early."""

from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Protocol, cast

import httpx
from pydantic import BaseModel
from sqlalchemy import Engine

from scan64.content.puzzle_import import (
    DEFAULT_PUZZLE_URL,
    ImportProgress,
    ImportSummary,
    download_puzzles,
)
from scan64.desktop_import import ImportCancelled, run_import


class JobSummary(BaseModel):
    accepted: int
    inserted: int
    existing: int
    read: int


class ImportJobState(BaseModel):
    status: Literal["idle", "running", "cancelling", "cancelled", "succeeded", "failed"] = "idle"
    phase: Literal["download", "read", "import", "save"] | None = None
    done: int = 0
    total: int | None = None
    unit: Literal["bytes", "puzzles"] = "bytes"
    bytes_per_second: float | None = None
    error: str | None = None
    summary: JobSummary | None = None


class JobAlreadyRunning(Exception):
    """A second start must not replace or cancel the current job."""


class _NetworkStream(Protocol):
    def get_extra_info(self, info: str) -> object: ...


class _DownloadTransport(httpx.HTTPTransport):
    """Interrupt even a stalled response body, not just the next progress callback."""

    def __init__(self, cancel: threading.Event) -> None:
        super().__init__()
        self.cancelled = cancel
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        stream = response.extensions.get("network_stream")
        connection = (
            cast(_NetworkStream, stream).get_extra_info("socket") if stream is not None else None
        )
        with self._lock:
            if isinstance(connection, socket.socket):
                self._socket = connection
            if self.cancelled.is_set():
                self._interrupt()
        return response

    def _interrupt(self) -> None:
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # The response may already have closed the socket.

    def interrupt(self) -> None:
        with self._lock:
            self._interrupt()


def validate_puzzle_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.name.endswith(".csv.zst"):
        raise ValueError("Choose a readable regular .csv.zst file with an absolute path")
    try:
        if not path.is_file():
            raise ValueError("Choose a readable regular .csv.zst file")
        with path.open("rb"):
            pass
    except OSError as error:
        raise ValueError("Choose a readable regular .csv.zst file") from error
    return path.resolve()


class PuzzleImportJob:
    def __init__(
        self, engine: Engine, data_dir: Path, *, download_url: str = DEFAULT_PUZZLE_URL
    ) -> None:
        self.engine = engine
        self.data_dir = data_dir
        self.download_url = download_url
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._committed = threading.Event()
        self._thread: threading.Thread | None = None
        self._transport: _DownloadTransport | None = None
        self._state = ImportJobState()
        self._phase_started = 0.0
        self._result: JobSummary | None = None
        # One desktop server owns this data directory. Recover only its temporary
        # download directories; never follow a link out to a user-selected file.
        for stale in data_dir.glob("puzzle-import-*"):
            try:
                if stale.is_symlink():
                    stale.unlink()
                elif stale.is_dir():
                    shutil.rmtree(stale)
            except OSError:
                # An antivirus scanner or another Windows file handle may still
                # hold an old partial file. Retry next launch; do not block startup.
                continue

    def state(self) -> ImportJobState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def start(self, source: Literal["download", "file"], path: str | None = None) -> ImportJobState:
        with self._lock:
            if self._state.status in {"running", "cancelling"}:
                raise JobAlreadyRunning()
            local = validate_puzzle_path(path or "") if source == "file" else None
            self._cancel.clear()
            self._committed.clear()
            self._result = None
            self._phase_started = time.monotonic()
            self._state = ImportJobState(
                status="running", phase="download" if local is None else "read"
            )
            self._thread = threading.Thread(target=self._run, args=(local,), daemon=True)
            self._thread.start()
            return self._state.model_copy(deep=True)

    def cancel(self) -> ImportJobState:
        with self._lock:
            if self._state.status == "running" and not self._committed.is_set():
                self._cancel.set()
                self._state.status = "cancelling"
            transport = self._transport
            result = self._state.model_copy(deep=True)
        if transport is not None and self._cancel.is_set():
            transport.interrupt()
        return result

    def close(self) -> None:
        self.cancel()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _progress(self, update: ImportProgress) -> None:
        if self._cancel.is_set():
            raise ImportCancelled()
        phase = "download" if update.phase == "connect" else update.phase
        with self._lock:
            if phase != self._state.phase:
                self._phase_started = time.monotonic()
            self._state.phase = phase
            self._state.done = update.done
            self._state.total = update.total
            self._state.unit = update.unit
            elapsed = time.monotonic() - self._phase_started
            self._state.bytes_per_second = (
                update.done / elapsed if update.unit == "bytes" and elapsed > 0 else None
            )
        if self._cancel.is_set():
            raise ImportCancelled()

    def _message(self, message: str) -> None:
        if message.startswith("Saving"):
            with self._lock:
                self._state.phase = "save"
                self._state.done = 0
                self._state.total = 1
                self._state.unit = "puzzles"
                self._state.bytes_per_second = None
        if self._cancel.is_set():
            raise ImportCancelled()

    def _record_summary(self, summary: ImportSummary) -> None:
        # Called under the commit lock before the irreversible write, so even a
        # later detach/temporary-file cleanup exception retains the true result.
        self._result = JobSummary(
            accepted=summary.accepted,
            inserted=summary.inserted,
            existing=summary.existing,
            read=summary.read,
        )

    def _finish_success(self, warning: str | None = None) -> None:
        # Caller holds the job lock.
        self._state.status = "succeeded"
        self._state.phase = "save"
        self._state.done = 1
        self._state.total = 1
        self._state.summary = self._result
        self._state.error = warning

    def _run(self, local: Path | None) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with TemporaryDirectory(prefix="puzzle-import-", dir=self.data_dir) as temporary:
                provenance = None
                path = local
                if path is None:
                    path = Path(temporary) / "puzzles.csv.zst"
                    transport = _DownloadTransport(self._cancel)
                    with self._lock:
                        self._transport = transport
                    try:
                        with httpx.Client(transport=transport, follow_redirects=True) as client:
                            provenance = download_puzzles(
                                path, url=self.download_url, client=client, progress=self._progress
                            )
                    finally:
                        with self._lock:
                            self._transport = None
                run_import(
                    self.engine,
                    path,
                    self._cancel,
                    self._message,
                    on_progress=self._progress,
                    commit_lock=self._lock,
                    committed=self._committed,
                    provenance=provenance,
                    before_commit=self._record_summary,
                )
            # Terminal status is published only after the downloaded file is removed.
            with self._lock:
                self._finish_success()
        except Exception as error:
            with self._lock:
                if self._committed.is_set():
                    self._finish_success(
                        "Puzzles were imported, but cleanup did not finish normally. "
                        "Restart Scan64 to retry temporary-file cleanup."
                    )
                else:
                    cancelled = self._cancel.is_set() or isinstance(error, ImportCancelled)
                    self._state.status = "cancelled" if cancelled else "failed"
                    # Never expose paths, credentials, URLs or internal traces.
                    self._state.error = None if cancelled else self._error_message(error)

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, httpx.HTTPError):
            return "The puzzle download failed. Check your connection and try again."
        if isinstance(error, (ValueError, OSError)):
            return "The puzzle file could not be read or imported. Choose a valid .csv.zst file."
        return "The puzzle import failed. Your puzzle catalog was not changed."
