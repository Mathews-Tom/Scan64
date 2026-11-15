from __future__ import annotations

import json
import os
import runpy
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64 import desktop_server
from scan64.api import web
from scan64.api.desktop_security import SHELL_ORIGINS, SHELL_TOKEN_HEADER
from scan64.content.puzzle_import import DEFAULT_PUZZLE_URL

TOKEN = "desktop-server-integration-secret"


@pytest.mark.parametrize("desktop", [False, True])
@pytest.mark.parametrize("debug_url", [None, "http://127.0.0.1:9876/fixture.csv.zst"])
def test_fixture_url_is_only_used_by_shell_import_lifespan(
    desktop: bool,
    debug_url: str | None,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCAN64_SHELL_TOKEN", raising=False)
    monkeypatch.delenv("SCAN64_DEBUG_PUZZLE_URL", raising=False)
    monkeypatch.setenv("SCAN64_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCAN64_ENGINE_POOL_ENABLED", "0")
    if desktop:
        monkeypatch.setenv("SCAN64_SHELL_TOKEN", TOKEN)
    if debug_url is not None:
        monkeypatch.setenv("SCAN64_DEBUG_PUZZLE_URL", debug_url)
    namespace = runpy.run_path(str(Path(web.__file__).with_name("app.py")))
    application = namespace["app"]
    with TestClient(application):
        if desktop:
            assert application.state.puzzle_import_job.download_url == (
                debug_url or DEFAULT_PUZZLE_URL
            )
        else:
            assert not hasattr(application.state, "puzzle_import_job")
    assert not hasattr(application.state, "puzzle_import_job")


def server_environment(tmp_path: Path, port: int) -> dict[str, str]:
    source = str(Path(desktop_server.__file__).resolve().parent.parent)
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([source, os.environ.get("PYTHONPATH", "")]),
        "BROWSER": "/usr/bin/true",
        "SCAN64_DATA_DIR": str(tmp_path / "desktop-data"),
        "SCAN64_PORT": str(port),
        "SCAN64_SHELL_TOKEN": TOKEN,
        "SCAN64_DATABASE_URL": f"sqlite:///{(tmp_path / 'do-not-open.db').as_posix()}",
    }


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def accepting_connections(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def stop_server(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            # The entrypoint owns this group; never kill pytest's process group.
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                process.kill()
        process.wait(timeout=5)


def test_module_launch_uses_private_data_and_real_tokened_api(tmp_path: Path) -> None:
    port = free_port()
    web_dist = tmp_path / "web"
    web_dist.mkdir()
    (web_dist / "index.html").write_text("desktop must not serve this page")
    environment = {**server_environment(tmp_path, port), "SCAN64_WEB_DIST": str(web_dist)}
    log_path = tmp_path / "server.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "scan64.desktop_server"],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1) as client:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, log_path.read_text()
                    try:
                        health = client.get("/health")
                        if health.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, log_path.read_text()
                    time.sleep(0.05)
                assert health.json() == {"status": "ok"}
                assert client.get("/v1/app/info").status_code == 401
                assert (
                    client.get("/v1/app/info", headers={SHELL_TOKEN_HEADER: "wrong"}).status_code
                    == 401
                )
                headers = {SHELL_TOKEN_HEADER: TOKEN}
                assert client.get("/v1/app/info", headers=headers).json() == {
                    "distribution": "desktop"
                }
                assert client.get("/", headers=headers).status_code == 404
                assert client.get("/assets/app.js", headers=headers).status_code == 404
                assert client.get("/docs").status_code == 401
                for origin in SHELL_ORIGINS:
                    preflight = client.options(
                        "/v1/players",
                        headers={
                            "Origin": origin,
                            "Access-Control-Request-Method": "POST",
                            "Access-Control-Request-Headers": SHELL_TOKEN_HEADER,
                        },
                    )
                    assert preflight.status_code == 200
                    assert preflight.headers["access-control-allow-origin"] == origin
                created = client.post("/v1/players", json={"id": "desktop-player"}, headers=headers)
                assert created.status_code == 200
                bearer = {"Authorization": f"Bearer {created.json()['access_token']}"}
                profile = "/v1/players/desktop-player/profile"
                assert client.get(profile, headers=headers).status_code == 401
                assert client.get(profile, headers=bearer).status_code == 401
                assert client.get(profile, headers={**headers, **bearer}).status_code == 200
                assert (
                    client.get(
                        profile,
                        headers={**headers, "Authorization": "Bearer somebody-else"},
                    ).status_code
                    == 403
                )
                assert (tmp_path / "desktop-data" / "scan64.db").is_file()
                assert not (tmp_path / "do-not-open.db").exists()
                started = time.monotonic()
                assert process.stdin is not None
                process.stdin.close()
                process.wait(timeout=4.9)
                assert time.monotonic() - started < 5
                assert not accepting_connections(port)
        finally:
            stop_server(process)
    assert TOKEN not in log_path.read_text()


# This real UCI subprocess deliberately stays alive on both "quit" and stdin EOF.
# It catches cleanup that only relies on python-chess/Stockfish cooperation.
ENGINE_SOURCE = r"""
import json
import os
import socket
import sys
import time
from pathlib import Path

listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen()
Path(sys.argv[1]).write_text(json.dumps({
    "pid": os.getpid(), "parent_pid": os.getppid(), "port": listener.getsockname()[1]
}))
for line in sys.stdin:
    if line.strip() == "uci":
        print("id name lifecycle-test-engine", flush=True)
        print("uciok", flush=True)
    elif line.strip() == "isready":
        print("readyok", flush=True)
while True:
    time.sleep(1)
"""

# Use the real entrypoint and watchdog, replacing only uvicorn's server so its
# shutdown never returns. This represents a blocked startup/lifespan or busy
# checked-out engine that the pool's close() cannot reach.
HUNG_SERVER_SOURCE = r"""
import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import chess.engine
from scan64 import desktop_server

class HungServer:
    def __init__(self, configuration):
        self.should_exit = False
        self.started = True

    def run(self):
        asyncio.run(self.serve())

    async def serve(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        transport, engine = await chess.engine.popen_uci([sys.executable, sys.argv[1], sys.argv[2]])
        Path(sys.argv[3]).write_text(json.dumps({
            "pid": os.getpid(), "port": listener.getsockname()[1], "engine_pid": transport.get_pid()
        }))
        await asyncio.Event().wait()

sys.modules["uvicorn"] = SimpleNamespace(Config=lambda *args, **kwargs: None, Server=HungServer)
desktop_server.main()
"""

SHELL_SOURCE = r"""
import subprocess
import sys

# This is the only owner of the write end of server stdin. Killing this process
# must release it; the engine's UCI pipes must not keep the shell pipe alive.
server = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE)
server.wait()
"""


@pytest.mark.parametrize("kill_shell", [False, True], ids=["stdin-eof", "shell-killed"])
def test_eof_deadline_kills_server_and_actual_uci_child_even_if_shutdown_hangs(
    tmp_path: Path, kill_shell: bool
) -> None:
    engine_script = tmp_path / "engine.py"
    engine_script.write_text(ENGINE_SOURCE)
    engine_record = tmp_path / "engine.json"
    server_record = tmp_path / "server.json"
    command = [
        sys.executable,
        "-c",
        HUNG_SERVER_SOURCE,
        str(engine_script),
        str(engine_record),
        str(server_record),
    ]
    if kill_shell:
        command = [sys.executable, "-c", SHELL_SOURCE, *command]
    log_path = tmp_path / "lifecycle.log"
    log = log_path.open("wb")
    process = subprocess.Popen(
        command,
        env=server_environment(tmp_path, free_port()),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=log,
    )
    server = None
    engine = None
    try:
        deadline = time.monotonic() + 15
        while not server_record.exists():
            assert process.poll() is None, log_path.read_text()
            assert time.monotonic() < deadline, "Lifecycle fixture failed to initialize"
            time.sleep(0.05)
        # File creation and write are not atomic; the socket must be live before
        # the fixture is considered ready, and JSON must have finished writing.
        while True:
            try:
                server = json.loads(server_record.read_text())
                engine = json.loads(engine_record.read_text())
                break
            except json.JSONDecodeError:
                assert time.monotonic() < deadline
                time.sleep(0.01)
        # Windows virtualenv python.exe is a redirector: asyncio tracks its PID,
        # while the real UCI interpreter is its direct child. Check that lineage
        # rather than confusing the redirector with the process owning the socket.
        assert server["engine_pid"] in (engine["pid"], engine["parent_pid"])
        assert accepting_connections(server["port"])
        assert accepting_connections(engine["port"])
        started = time.monotonic()
        if kill_shell:
            process.kill()
        else:
            assert process.stdin is not None
            process.stdin.close()
        process.wait(timeout=4.9)
        while accepting_connections(server["port"]) or accepting_connections(engine["port"]):
            assert time.monotonic() - started < 5, "Server or UCI child survived shell death"
            time.sleep(0.1)
        assert time.monotonic() - started < 5
    finally:
        stop_server(process)
        # If an assertion failed, do not leave the deliberately uncooperative
        # fixture engine running on the developer's machine.
        for record in [server, engine]:
            if record is not None and accepting_connections(record["port"]):
                try:
                    os.kill(record["pid"], 9)
                except ProcessLookupError:
                    pass
        log.close()
