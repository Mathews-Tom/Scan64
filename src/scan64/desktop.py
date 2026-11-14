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
    os.environ["SCAN64_DISTRIBUTION"] = "desktop"
    os.environ["PATH"] = (
        str(resources / "bin")
        + os.pathsep
        + os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    )
    # PATH lookup leaves argv[0] as "stockfish", so sf_18 searches the working
    # directory for external NNUE files. All writable paths remain absolute.
    os.chdir(resources / "bin")


def reserve_port(start: int = 8001) -> socket.socket:
    """Keep the selected loopback socket bound until uvicorn takes ownership."""
    for port in range(start, 65536):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def run_window(
    instance: InstanceLock,
    listener: socket.socket,
    *,
    no_browser: bool = False,
    import_path: Path | None = None,
) -> None:
    # Import the application only after the persistent paths are configured.
    import tkinter as tk
    from queue import Empty, Queue
    from tkinter import filedialog

    import uvicorn

    from scan64.desktop_import import ImportCancelled, run_import
    from scan64.persistence.database import engine

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
    import_cancel = threading.Event()
    import_messages: Queue[str] = Queue()
    importer: threading.Thread | None = None

    def import_file(selected: Path | None = None) -> None:
        nonlocal importer
        path = (
            str(selected)
            if selected
            else filedialog.askopenfilename(
                parent=root,
                title="Import Lichess puzzles",
                filetypes=[("Compressed puzzle CSV", "*.csv.zst")],
            )
        )
        if not path:
            return
        import_cancel.clear()
        import_button.config(state="disabled")
        cancel_button.config(state="normal")

        def run() -> None:
            try:
                summary = run_import(engine, Path(path), import_cancel, import_messages.put)
                import_messages.put(
                    f"Imported {summary.inserted} puzzles; {summary.existing} already present."
                )
            except ImportCancelled:
                import_messages.put("Import cancelled. Catalog unchanged.")
            except Exception as error:
                import_messages.put(f"Import failed. Catalog unchanged.\n{error}")

        importer = threading.Thread(target=run, name="scan64-import")
        importer.start()

    def open_browser() -> None:
        if server.started and not quitting and not no_browser:
            webbrowser.open(url)

    def quit_app() -> None:
        nonlocal quitting
        quitting = True
        import_cancel.set()
        label.config(text="Stopping server and chess engines…")
        server.should_exit = True
        if not worker.is_alive():
            root.destroy()

    tk.Button(root, text="Open Scan64", command=open_browser).pack(padx=20, pady=5)
    import_button = tk.Button(root, text="Import puzzles…", command=import_file)
    import_button.pack(padx=20, pady=5)
    cancel_button = tk.Button(
        root, text="Cancel import", command=import_cancel.set, state="disabled"
    )
    cancel_button.pack(padx=20, pady=5)
    import_status = tk.Label(root, text="", padx=20, pady=5, wraplength=440)
    import_status.pack()
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
        try:
            while True:
                import_status.config(text=import_messages.get_nowait())
        except Empty:
            pass
        if importer is not None and not importer.is_alive() and not quitting:
            import_button.config(state="normal")
            cancel_button.config(state="disabled")
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
            if import_path is not None:
                import_file(import_path)
            open_browser()
        root.after(100, poll)

    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        server.should_exit = True
        worker.join()
        import_cancel.set()
        if importer is not None:
            importer.join()


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Scan64 desktop launcher")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--import-puzzles", type=Path, help="Select a local file for the window importer"
    )
    args = parser.parse_args(argv if argv is not None else [])
    data = data_directory()
    resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    configure_environment(resources, data)
    # Windowed PyInstaller apps have no stdout/stderr. Retain startup diagnostics.
    with (data / "desktop.log").open("a", buffering=1) as log:
        previous_stdout, previous_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = log, log
        instance = InstanceLock(data)
        listener: socket.socket | None = None
        try:
            if not instance.acquire():
                if not args.no_browser:
                    webbrowser.open(instance.existing_url())
                return
            listener = reserve_port()
            run_window(
                instance, listener, no_browser=args.no_browser, import_path=args.import_puzzles
            )
        finally:
            if listener is not None:
                listener.close()
            instance.close()
            sys.stdout, sys.stderr = previous_stdout, previous_stderr


if __name__ == "__main__":
    main(sys.argv[1:])
