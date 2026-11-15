"""Loopback-only server owned by the desktop shell's open stdin pipe.

Run with ``python -m scan64.desktop_server``. The shell supplies SCAN64_DATA_DIR,
SCAN64_PORT and SCAN64_SHELL_TOKEN, and retains stdin until it quits. No browser
or platform-specific advisory file lock is involved.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, NoReturn, cast

# Leave margin under the shell's five-second kill deadline. This timer does not
# depend on uvicorn's event loop, lifespan, engine pool, or graceful shutdown.
PARENT_EXIT_TIMEOUT = 3.0
_process_group: int | None = None
_windows_job: int | None = None


@dataclass(frozen=True)
class DesktopConfiguration:
    data_dir: Path
    port: int
    token: str = field(repr=False)


def configuration_from_environment(environment: Mapping[str, str]) -> DesktopConfiguration:
    data_dir = environment.get("SCAN64_DATA_DIR", "")
    if not data_dir.strip():
        raise ValueError("SCAN64_DATA_DIR is required")
    raw_port = environment.get("SCAN64_PORT", "")
    try:
        port = int(raw_port)
    except ValueError:
        raise ValueError("SCAN64_PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("SCAN64_PORT must be an integer between 1 and 65535")
    token = environment.get("SCAN64_SHELL_TOKEN", "")
    if not token or not token.isascii() or not token.isprintable() or token.isspace():
        raise ValueError("SCAN64_SHELL_TOKEN must be a nonempty printable ASCII token")
    return DesktopConfiguration(Path(data_dir).expanduser().resolve(), port, token)


def configure_environment(configuration: DesktopConfiguration) -> None:
    configuration.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.platform != "win32":
        configuration.data_dir.chmod(0o700)
    os.environ["SCAN64_DATA_DIR"] = str(configuration.data_dir)
    # Set this before the application's first import: the database engine is
    # constructed at import time. Never inherit a browser installation's DB URL.
    database_path = (configuration.data_dir / "scan64.db").as_posix()
    os.environ["SCAN64_DATABASE_URL"] = f"sqlite:///{database_path}"
    os.environ["SCAN64_SHELL_TOKEN"] = configuration.token
    os.environ["SCAN64_DISTRIBUTION"] = "desktop"


def _create_windows_job() -> int:
    """Assign this server to a non-inheritable kill-on-close Windows Job.

    python-chess creates both pooled and per-call engine children. All inherit
    job membership (not this handle); abrupt server exit closes the sole handle
    and kills them, including an engine busy outside the pool's idle queue.
    Failure is fatal rather than silently dropping the child-death guarantee.
    """
    if sys.platform != "win32":
        raise RuntimeError("Windows Job Objects require Windows")
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = cast(int, kernel32.CreateJobObjectW(None, None))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    try:
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(information), ctypes.sizeof(information)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    return handle


def _configure_process_cleanup() -> None:
    global _process_group, _windows_job
    if sys.platform == "win32":
        _windows_job = _create_windows_job()
    else:
        # Never signal the shell's group. A launcher that already made us a group
        # leader has done the isolation; otherwise create our own session/group.
        if os.getpgrp() != os.getpid():
            os.setsid()
        _process_group = os.getpid()


def _exit_process_tree(exit_code: int) -> NoReturn:
    if sys.platform != "win32" and _process_group == os.getpid():
        try:
            # Atomic group kill covers engines started concurrently with EOF,
            # unlike enumerating and killing one snapshot of descendant PIDs.
            os.killpg(_process_group, signal.SIGKILL)
        finally:
            os._exit(exit_code)
    # On Windows the kernel closes our sole Job handle even for os._exit; the
    # server and every descendant are killed without needing UCI cooperation.
    os._exit(exit_code)


def _watch_parent(stream: BinaryIO, stop_requested: threading.Event) -> None:
    try:
        while stream.read(1):
            pass
    except (OSError, ValueError):
        # A broken/closed parent pipe is equivalent to EOF, not permission to
        # keep serving indefinitely.
        pass
    finally:
        deadline = threading.Timer(PARENT_EXIT_TIMEOUT, _exit_process_tree, args=(0,))
        deadline.daemon = True
        deadline.start()
        stop_requested.set()


def _open_windows_parent_pipe() -> BinaryIO:
    """Recover STARTF_USESTDHANDLES stdin when a windowed bootloader clears sys.stdin."""
    if sys.platform != "win32":
        raise RuntimeError("Windows pipe handles require Windows")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    handle = kernel.GetStdHandle(-10)  # STD_INPUT_HANDLE
    if not handle or handle == wintypes.HANDLE(-1).value or kernel.GetFileType(handle) != 3:
        raise RuntimeError("Desktop server requires a piped stdin held by its shell")
    # open_osfhandle transfers ownership to the descriptor; no console is attached
    # or allocated. Never let an engine inherit the shell's watchdog handle.
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    try:
        os.set_inheritable(descriptor, False)
        return cast(BinaryIO, os.fdopen(descriptor, "rb", buffering=0))
    except BaseException:
        os.close(descriptor)
        raise


def _parent_stream() -> BinaryIO:
    if sys.stdin is not None and not sys.stdin.isatty():
        return sys.stdin.buffer
    if sys.stdin is None and sys.platform == "win32":
        return _open_windows_parent_pipe()
    raise RuntimeError("Desktop server requires a piped stdin held by its shell")


def main() -> NoReturn:
    _configure_process_cleanup()
    exit_code = 1
    try:
        parent_stream = _parent_stream()
        stop_requested = threading.Event()
        threading.Thread(
            target=_watch_parent,
            args=(parent_stream, stop_requested),
            daemon=True,
            name="desktop-parent-watchdog",
        ).start()
        configuration = configuration_from_environment(os.environ)
        configure_environment(configuration)
        # Windowed PyInstaller deliberately sets absent standard streams to None.
        # Uvicorn's formatters need real streams; retain diagnostics without ever
        # creating a console or recording request headers/the shell token.
        if sys.platform == "win32" and (sys.stdout is None or sys.stderr is None):
            logs = configuration.data_dir / "logs"
            logs.mkdir(mode=0o700, exist_ok=True)
            log = (logs / "server.log").open("a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = log
            if sys.stderr is None:
                sys.stderr = log

        # Imports, schema migration and lifespan startup are inside the watchdog
        # deadline too. No application module may precede configure_environment.
        import uvicorn

        server = uvicorn.Server(
            uvicorn.Config(
                "scan64.api.app:app",
                host="127.0.0.1",
                port=configuration.port,
                access_log=False,
                timeout_graceful_shutdown=2,
            )
        )

        def request_shutdown() -> None:
            stop_requested.wait()
            server.should_exit = True

        threading.Thread(target=request_shutdown, daemon=True).start()
        server.run()
        exit_code = 0 if server.started else 1
    except SystemExit as error:
        exit_code = error.code if isinstance(error.code, int) else 1
    except KeyboardInterrupt:
        exit_code = 130
    except Exception:
        traceback.print_exc()
    finally:
        # Also cover bind/startup failures and a server that stops before EOF.
        # The POSIX process deliberately exits by SIGKILL after graceful app
        # cleanup so no residual engine can outlive it.
        _exit_process_tree(exit_code)


if __name__ == "__main__":
    main()
