"""Prove a real bundled-origin fetch and bounded shell/server lifetime in CI."""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


def alive(pid: int) -> bool:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def assert_stopped(pid: int, port: int) -> None:
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if alive(pid):
        raise AssertionError(f"Server PID {pid} survived shell termination for five seconds")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise AssertionError(f"Server port {port} is still listening")


def run(binary: Path, directory: Path, *, kill: bool) -> dict[str, object]:
    record_path = directory / "roundtrip.json"
    environment = dict(os.environ)
    environment.update(
        BROWSER="/usr/bin/true",
        SCAN64_SERVER_COMMAND=json.dumps([sys.executable, "-m", "scan64.desktop_server"]),
        SCAN64_DATA_DIR=str(directory / "data"),
        SCAN64_SMOKE_FILE=str(record_path),
    )
    if kill:
        environment["SCAN64_SMOKE_HOLD"] = "1"
    else:
        environment.pop("SCAN64_SMOKE_HOLD", None)
    process = subprocess.Popen([str(binary)], env=environment)
    try:
        deadline = time.monotonic() + 175
        while not record_path.exists():
            failure = record_path.with_suffix(".failed.json")
            if failure.exists():
                raise AssertionError(f"Page-origin smoke failed: {failure.read_text()}")
            if process.poll() is not None:
                raise AssertionError(f"Shell exited before smoke record: {process.returncode}")
            if time.monotonic() >= deadline:
                raise AssertionError("No tokened request record before smoke deadline")
            time.sleep(0.1)
        # Rust writes the file once; wait for its complete JSON before reading.
        record = json.loads(record_path.read_text())
        expected_origin = "http://tauri.localhost" if os.name == "nt" else "tauri://localhost"
        assert record["origin"] == expected_origin, record
        assert record["path"] == "/v1/players", record
        assert record["status"] == 200, record
        assert record["shell_pid"] == process.pid, record
        assert record["windows"] == 1, record
        assert "token" not in record, "Smoke receipts must not contain credentials"
        server_pid, port = int(record["server_pid"]), int(record["port"])
        if kill:
            try:
                with urlopen(f"http://127.0.0.1:{port}/v1/players", timeout=5):
                    raise AssertionError("Desktop API accepted a request without shell token")
            except HTTPError as error:
                assert error.code == 401, error.code
            process.kill()  # SIGKILL / TerminateProcess: no graceful shell cleanup.
            process.wait(timeout=5)
        else:
            assert process.wait(timeout=10) == 0
        assert_stopped(server_pid, port)
        print(json.dumps({"phase": "kill" if kill else "quit", **record}, sort_keys=True))
        return record
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        log = directory / "data" / "logs" / "server.log"
        if log.exists():
            print(log.read_text(encoding="utf-8", errors="replace"))


def main() -> None:
    binary = Path(sys.argv[1]).resolve(strict=True)
    output = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="scan64-desktop-") as temporary:
        root = Path(temporary)
        records = []
        for kill, name in [(False, "quit"), (True, "kill")]:
            directory = root / name
            directory.mkdir()
            records.append(run(binary, directory, kill=kill))
        output.write_text(json.dumps(records, indent=2) + "\n")
    print("Desktop smoke passed: origin fetch, player creation, token gate, quit and kill")


if __name__ == "__main__":
    main()
