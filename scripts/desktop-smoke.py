"""Prove a real bundled-origin fetch and bounded shell/server lifetime in CI."""

from __future__ import annotations

import argparse
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
        if sys.platform == "linux":
            status = Path(f"/proc/{pid}/stat")
            if status.exists() and status.read_text().split(") ", 1)[1].startswith("Z"):
                return False
        os.kill(pid, 0)
    except (ProcessLookupError, FileNotFoundError):
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


def descendants(parent: int) -> list[int]:
    """Snapshot actual children, including live bundled engine processes."""
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId"
                " | ConvertTo-Json -Compress",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        rows = [(item["ProcessId"], item["ParentProcessId"]) for item in json.loads(result.stdout)]
    else:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], check=True, capture_output=True, text=True
        )
        rows = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
    children: list[int] = []
    pending = [parent]
    while pending:
        current = pending.pop()
        direct = [pid for pid, ppid in rows if ppid == current]
        children.extend(direct)
        pending.extend(direct)
    return children


def assert_children_stopped(children: list[int]) -> None:
    deadline = time.monotonic() + 5
    while any(alive(pid) for pid in children) and time.monotonic() < deadline:
        time.sleep(0.05)
    survivors = [pid for pid in children if alive(pid)]
    if survivors:
        raise AssertionError(f"Child processes survived shell termination: {survivors}")


def runtime_path() -> str:
    """Exclude Python, MinGW and other build tools from installed-app acceptance."""
    if os.name == "nt":
        system = Path(os.environ["SystemRoot"])
        return os.pathsep.join(str(path) for path in (system / "System32", system))
    return "/usr/bin:/bin:/usr/sbin:/sbin"


def run(
    binary: Path,
    directory: Path,
    *,
    kill: bool,
    packaged: bool = False,
    screenshot: Path | None = None,
) -> dict[str, object]:
    record_path = directory / "roundtrip.json"
    environment = dict(os.environ)
    environment.update(
        BROWSER="/usr/bin/true",
        SCAN64_DATA_DIR=str(directory / "data"),
        SCAN64_SMOKE_FILE=str(record_path),
    )
    if packaged:
        environment.pop("SCAN64_SERVER_COMMAND", None)
        environment["PATH"] = runtime_path()
    else:
        environment["SCAN64_SERVER_COMMAND"] = json.dumps(
            [sys.executable, "-m", "scan64.desktop_server"]
        )
    if screenshot is not None:
        environment["SCAN64_SMOKE_BOARD"] = "1"
    if kill:
        environment["SCAN64_SMOKE_HOLD"] = "1"
    else:
        environment.pop("SCAN64_SMOKE_HOLD", None)
    process = subprocess.Popen([str(binary)], env=environment)
    children: list[int] = []
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
            children = descendants(process.pid)
            assert server_pid in children, "Receipt server is not a child of the installed app"
            if packaged:
                assert len(children) > 1, "Installed app did not launch any engine children"
        if screenshot is not None:
            # The ordinary analysis screen mounts after initializeDesktop returns.
            time.sleep(5)
            subprocess.run(["import", "-window", "root", str(screenshot)], check=True)
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
        assert_children_stopped(children)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--packaged", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix="scan64-desktop-") as temporary:
        root = Path(temporary)
        records = []
        for kill, name in [(False, "quit"), (True, "kill")]:
            directory = root / name
            directory.mkdir()
            records.append(
                run(
                    binary,
                    directory,
                    kill=kill,
                    packaged=args.packaged,
                    screenshot=args.screenshot if kill else None,
                )
            )
        output.write_text(json.dumps(records, indent=2) + "\n")
    print("Desktop smoke passed: origin fetch, player creation, token gate, quit and kill")


if __name__ == "__main__":
    main()
