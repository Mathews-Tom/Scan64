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
from importlib.metadata import version
from pathlib import Path
from typing import IO

from scan64.desktop_update import (
    ReleaseClient,
    UpdateController,
    app_bundle,
    bundle_version,
    set_update_controller,
    unavailable_reason,
)


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
    updates: UpdateController | None = None,
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
        restart_button.config(state="disabled")
        check_button.config(state="disabled")
        if updates is not None:
            updates.stop()
        import_cancel.set()
        label.config(text="Stopping server and chess engines…")
        server.should_exit = True
        if not worker.is_alive():
            root.destroy()

    def restart_update() -> None:
        if updates is None:
            return
        try:
            updates.restart()
        except Exception as error:
            update_status.config(text=f"Update failed: {error}")
            return
        quit_app()

    tk.Label(root, text=f"Scan64 {version('scan64')}").pack(padx=20, pady=5)
    update_status = tk.Label(root, text="", padx=20, pady=5, wraplength=440)
    update_status.pack()
    check_button = tk.Button(
        root, text="Check for updates", command=updates.check_background if updates else ""
    )
    check_button.pack(padx=20, pady=5)
    restart_button = tk.Button(
        root, text="Restart to update", command=restart_update, state="disabled"
    )
    restart_button.pack(padx=20, pady=5)
    automatic = tk.BooleanVar(value=updates.snapshot()["automatic_updates"] if updates else True)

    def set_automatic() -> None:
        if updates is not None:
            try:
                updates.set_automatic(automatic.get())
            except OSError as error:
                automatic.set(updates.snapshot()["automatic_updates"])
                update_status.config(text=f"Could not save update settings: {error}")

    tk.Checkbutton(root, text="Automatic updates", variable=automatic, command=set_automatic).pack(
        padx=20, pady=5
    )
    if updates is not None and updates.disabled_reason:
        tk.Button(
            root,
            text="Download from GitHub",
            command=lambda: (
                webbrowser.open(updates.snapshot()["release_url"]) if not no_browser else None
            ),
        ).pack(padx=20, pady=5)

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
        if updates is not None:
            state = updates.snapshot()
            update_status.config(text=state["message"])
            check_button.config(
                state="normal"
                if not quitting
                and state["available"]
                and state["status"] not in {"checking", "downloading", "ready", "installing"}
                else "disabled"
            )
            restart_button.config(
                state="normal" if not quitting and state["status"] == "ready" else "disabled"
            )
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
        if updates is not None:
            updates.stop()
        server.should_exit = True
        worker.join()
        import_cancel.set()
        if importer is not None:
            importer.join()


def _temporary_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(Path("/tmp").resolve()):
        raise ValueError("Update test paths must be below /tmp")
    return resolved


def run_update_test(config_path: Path) -> None:
    """Explicit disposable-app seam; impossible to target the real app or data.

    No environment trust override exists. Both the frozen executable's bundle
    and HOME must resolve below /tmp, and relaunch is a direct temporary binary.
    Production GUI/API entry points always use only the committed public key.
    """
    import json

    bundle = app_bundle()
    if not getattr(sys, "frozen", False) or bundle is None:
        raise ValueError("Update e2e requires a frozen disposable app")
    bundle = _temporary_path(bundle)
    home = _temporary_path(Path.home())
    config = json.loads(_temporary_path(config_path).read_text())
    result = _temporary_path(Path(config["result_path"]))
    command = config.get("relaunch_command")
    if command is not None:
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) for item in command)
        ):
            raise ValueError("Invalid test relaunch command")
        if not Path(command[0]).is_absolute():
            raise ValueError("Test relaunch executable must be absolute")
        _temporary_path(Path(command[0]))
    os.environ["BROWSER"] = "/usr/bin/true"
    data = _temporary_path(data_directory(home))
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    action = config.get("action")
    if action == "probe":
        resources = Path(getattr(sys, "_MEIPASS"))
        configure_environment(resources, data)
        from sqlmodel import Session, select

        from scan64.api.app import app, lifespan
        from scan64.chess.games.models import Game, PlaySession
        from scan64.persistence.database import engine

        async def probe() -> dict[str, object]:
            async with lifespan(app):
                with Session(engine) as session:
                    return {
                        "status": "relaunched",
                        "startup": "ok",
                        "played_games": len(
                            session.exec(select(Game).where(Game.result == "0-1")).all()
                        ),
                        "completed_sessions": len(
                            session.exec(
                                select(PlaySession).where(PlaySession.status == "completed")
                            ).all()
                        ),
                    }

        state = asyncio.run(probe())
        engine.dispose()
    elif action in {"check", "restart"}:
        updates = UpdateController(
            data,
            version("scan64"),
            bundle,
            public_key=config["public_key"],
            release_url=config["release_url"],
            client=ReleaseClient(test_loopback=True),
            relaunch_command=command,
            disabled_reason=unavailable_reason(bundle),
        )
        updates.check()
        state = updates.snapshot()
        if action == "restart" and state["status"] == "ready":
            updates.restart()
    else:
        raise ValueError("Invalid update test action")
    state.update(current_version=version("scan64"), bundle_version=bundle_version(bundle))
    result.write_text(json.dumps(state))


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Scan64 desktop launcher")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--import-puzzles", type=Path, help="Select a local file for the window importer"
    )
    parser.add_argument("--update-test-config", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv if argv is not None else [])
    if args.update_test_config is not None:
        run_update_test(args.update_test_config)
        return
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
            bundle = app_bundle()
            updates = UpdateController(
                data, version("scan64"), bundle, disabled_reason=unavailable_reason(bundle)
            )
            set_update_controller(updates)
            updates.start()
            run_window(
                instance,
                listener,
                no_browser=args.no_browser,
                import_path=args.import_puzzles,
                updates=updates,
            )
        finally:
            if listener is not None:
                listener.close()
            instance.close()
            set_update_controller(None)
            sys.stdout, sys.stderr = previous_stdout, previous_stderr


if __name__ == "__main__":
    main(sys.argv[1:])
