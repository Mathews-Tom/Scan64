"""macOS app entry point; source and container entry points remain independent."""

from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import IO


def data_directory(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Application Support" / "Scan64"


def configure_environment(resources: Path, data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["SCAN64_DATABASE_URL"] = f"sqlite:///{data / 'scan64.db'}"
    os.environ["SCAN64_DATA_DIR"] = str(data)
    os.environ["SCAN64_WEB_DIST"] = str(resources / "web")
    os.environ["PATH"] = (
        str(resources / "bin")
        + os.pathsep
        + os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    )


def reserve_port(start: int = 8001) -> socket.socket:
    """Keep the selected loopback socket bound until uvicorn takes ownership."""
    for port in range(start, 65536):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            listener.close()
            continue
        return listener
    raise RuntimeError("No free loopback port is available")


class InstanceLock:
    """An OS lock survives stale metadata and is automatically freed on a crash."""

    def __init__(self, directory: Path) -> None:
        fd = os.open(directory / "instance.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.file: IO[str] = os.fdopen(fd, "r+")
        self.owned = False

    def acquire(self) -> bool:
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        self.owned = True
        self.file.seek(0)
        self.file.truncate()
        self.file.flush()
        return True

    def publish(self, port: int) -> None:
        self.file.seek(0)
        self.file.truncate()
        self.file.write(str(port))
        self.file.flush()

    def existing_url(self, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.file.seek(0)
            value = self.file.read().strip()
            if value.isdecimal() and 8001 <= int(value) <= 65535:
                return f"http://127.0.0.1:{int(value)}"
            time.sleep(0.05)
        raise RuntimeError("The existing Scan64 instance did not finish starting")

    def close(self) -> None:
        if self.owned:
            self.file.seek(0)
            self.file.truncate()
            self.file.flush()
            fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


def run_window(instance: InstanceLock, listener: socket.socket) -> None:
    # Import the application only after the persistent paths are configured.
    import tkinter as tk

    import uvicorn

    port = int(listener.getsockname()[1])
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config("scan64.api.app:app", host="127.0.0.1", port=port, loop="asyncio")
    )
    failures: list[BaseException] = []

    def serve() -> None:
        try:
            asyncio.run(server.serve(sockets=[listener]))
        except BaseException as error:
            failures.append(error)

    root = tk.Tk()
    root.title("Scan64")
    root.resizable(False, False)
    label = tk.Label(root, text="Starting Scan64…", padx=30, pady=20)
    label.pack()
    opened = False
    quitting = False

    def open_browser() -> None:
        if server.started and not quitting:
            webbrowser.open(url)

    def quit_app() -> None:
        nonlocal quitting
        quitting = True
        label.config(text="Stopping server and chess engines…")
        server.should_exit = True
        if not worker.is_alive():
            root.destroy()

    tk.Button(root, text="Open Scan64", command=open_browser).pack(padx=20, pady=5)
    tk.Button(root, text="Quit Scan64", command=quit_app).pack(padx=20, pady=(5, 20))
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.createcommand("::tk::mac::Quit", quit_app)
    root.createcommand("::tk::mac::ReopenApplication", open_browser)
    signal.signal(signal.SIGTERM, lambda *_: quit_app())
    signal.signal(signal.SIGINT, lambda *_: quit_app())
    worker = threading.Thread(target=serve, name="scan64-server")
    worker.start()

    def poll() -> None:
        nonlocal opened
        if not worker.is_alive():
            if failures and not quitting:
                label.config(text=f"Scan64 could not start. See desktop.log.\n{failures[0]}")
                root.after(100, poll)
                return
            root.destroy()
            return
        if server.started and not opened and not quitting:
            instance.publish(port)
            label.config(text=f"Scan64 is running\n{url}")
            opened = True
            open_browser()
        root.after(100, poll)

    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        server.should_exit = True
        worker.join()


def main() -> None:
    data = data_directory()
    resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    configure_environment(resources, data)
    # Windowed PyInstaller apps have no stdout/stderr. Retain startup diagnostics.
    with (data / "desktop.log").open("a", buffering=1) as log:
        sys.stdout, sys.stderr = log, log
        instance = InstanceLock(data)
        listener: socket.socket | None = None
        try:
            if not instance.acquire():
                webbrowser.open(instance.existing_url())
                return
            listener = reserve_port()
            run_window(instance, listener)
        finally:
            if listener is not None:
                listener.close()
            instance.close()


if __name__ == "__main__":
    main()
