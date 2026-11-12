import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import uvicorn

from scan64.api.web import web_dist_directory
from scan64.cli.main import main
from scan64.cli.serve import _run_server, serve_command


def test_serve_dispatch_uses_loopback_and_opens_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["scan64-cli", "serve"])
    with patch("scan64.cli.main.serve_command") as serve:
        main()
    serve.assert_called_once_with("127.0.0.1", 8001, open_browser=True)


def test_serve_dispatch_accepts_container_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv", ["scan64-cli", "serve", "--host", "0.0.0.0", "--port", "8061", "--no-browser"]
    )
    with patch("scan64.cli.main.serve_command") as serve:
        main()
    serve.assert_called_once_with("0.0.0.0", 8061, open_browser=False)


@pytest.mark.parametrize("port", ["0", "65536"])
def test_serve_rejects_invalid_ports(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    monkeypatch.setattr("sys.argv", ["scan64-cli", "serve", "--port", port])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_missing_stockfish_exits_with_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SystemExit, match="stockfish.*PATH"):
        serve_command("127.0.0.1", 8001, open_browser=False)


def test_missing_client_exits_with_build_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN64_WEB_DIST", str(tmp_path))
    with patch("scan64.cli.serve.shutil.which", return_value="/usr/bin/stockfish"):
        with pytest.raises(SystemExit, match="pnpm.*build"):
            serve_command("127.0.0.1", 8001, open_browser=False)
    assert web_dist_directory() == tmp_path


@pytest.mark.asyncio
async def test_browser_opens_only_after_server_started() -> None:
    server = Mock(spec=uvicorn.Server)
    server.started = False
    opened = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def serve() -> None:
        await asyncio.sleep(0)
        server.started = True
        await opened.wait()

    def open_browser(url: str) -> bool:
        assert server.started
        assert url == "http://127.0.0.1:8061"
        loop.call_soon_threadsafe(opened.set)
        return True

    server.serve = serve
    with patch("scan64.cli.serve.webbrowser.open", side_effect=open_browser) as browser:
        await _run_server(server, "http://127.0.0.1:8061", True)
    browser.assert_called_once()


@pytest.mark.asyncio
async def test_headless_server_never_opens_browser() -> None:
    server = Mock(spec=uvicorn.Server)
    server.serve = AsyncMock()
    with patch("scan64.cli.serve.webbrowser.open") as browser:
        await _run_server(server, "http://127.0.0.1:8061", False)
    server.serve.assert_awaited_once()
    browser.assert_not_called()


def test_ctrl_c_is_a_clean_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setenv("SCAN64_WEB_DIST", str(tmp_path))
    with (
        patch("scan64.cli.serve.shutil.which", return_value="/usr/bin/stockfish"),
        patch("scan64.cli.serve._run_server", new=AsyncMock(side_effect=KeyboardInterrupt)) as run,
    ):
        serve_command("127.0.0.1", 8001, open_browser=False)
    run.assert_awaited_once()


@pytest.mark.parametrize(
    ("host", "browser_host"),
    [("0.0.0.0", "127.0.0.1"), ("::", "[::1]"), ("::1", "[::1]")],
)
def test_browser_uses_reachable_loopback_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, browser_host: str
) -> None:
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setenv("SCAN64_WEB_DIST", str(tmp_path))
    with (
        patch("scan64.cli.serve.shutil.which", return_value="/usr/bin/stockfish"),
        patch("scan64.cli.serve._run_server", new=AsyncMock()) as run,
    ):
        serve_command(host, 8001, open_browser=True)
    server, url, open_browser = run.await_args.args
    assert server.config.host == host
    assert url == f"http://{browser_host}:8001"
    assert open_browser is True
