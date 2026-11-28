import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import uvicorn

from scan64.api.web import web_dist_directory
from scan64.cli.main import main
from scan64.cli.serve import _run_server, serve_command
from scan64.providers.engine.config import (
    ENGINE_CONFIG_ENV,
    LADDER_ENGINE_CONFIG_ENV,
    STOCKFISH_PATH_ENV,
)


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
    monkeypatch.delenv(ENGINE_CONFIG_ENV, raising=False)
    monkeypatch.delenv(STOCKFISH_PATH_ENV, raising=False)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SystemExit, match="stockfish.*PATH"):
        serve_command("127.0.0.1", 8001, open_browser=False)


def executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


POSIX_EXECUTABLE = pytest.mark.skipif(
    sys.platform == "win32", reason="the test binaries use the POSIX executable bit"
)


def no_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The engine check passes when serve reaches the web client check."""
    monkeypatch.setenv("SCAN64_WEB_DIST", str(tmp_path / "no-client"))


@POSIX_EXECUTABLE
def test_default_profile_checks_stockfish_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENGINE_CONFIG_ENV, raising=False)
    monkeypatch.delenv(STOCKFISH_PATH_ENV, raising=False)
    executable(tmp_path / "stockfish")
    monkeypatch.setenv("PATH", str(tmp_path))
    no_client(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="pnpm.*build"):
        serve_command("127.0.0.1", 8001, open_browser=False)


@POSIX_EXECUTABLE
def test_stockfish_path_replaces_the_path_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENGINE_CONFIG_ENV, raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv(STOCKFISH_PATH_ENV, str(executable(tmp_path / "sf18")))
    no_client(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="pnpm.*build"):
        serve_command("127.0.0.1", 8001, open_browser=False)
    monkeypatch.setenv(STOCKFISH_PATH_ENV, str(tmp_path / "missing"))
    with pytest.raises(SystemExit, match=f"{STOCKFISH_PATH_ENV}=.*missing"):
        serve_command("127.0.0.1", 8001, open_browser=False)


@POSIX_EXECUTABLE
def test_non_stockfish_profile_does_not_need_stockfish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = executable(tmp_path / "ethereal")
    profile = tmp_path / "engine.toml"
    profile.write_text(f"[engine]\nname = \"ethereal\"\nbinary_path = '{binary}'\n")
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(profile))
    monkeypatch.setenv("PATH", "")
    no_client(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="pnpm.*build"):
        serve_command("127.0.0.1", 8001, open_browser=False)


def test_missing_profile_binary_names_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "engine.toml"
    profile.write_text('[engine]\nname = "ethereal"\nbinary_path = "ethereal-missing"\n')
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(profile))
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SystemExit) as error:
        serve_command("127.0.0.1", 8001, open_browser=False)
    message = str(error.value)
    assert str(profile) in message and "(ethereal)" in message
    assert "ethereal-missing" in message and "Stockfish" not in message


def test_invalid_profile_exits_with_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(tmp_path / "absent.toml"))
    with pytest.raises(SystemExit, match="Engine configuration is invalid: .*absent.toml"):
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


def test_missing_ladder_profile_binary_names_the_ladder_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis = tmp_path / "engine.toml"
    analysis.write_text('[engine]\nname = "ethereal"\nbinary_path = "ethereal-missing"\n')
    ladder = tmp_path / "ladder.toml"
    ladder.write_text('[engine]\nname = "arasan"\nbinary_path = "arasan-missing"\n')
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(analysis))
    monkeypatch.setenv(LADDER_ENGINE_CONFIG_ENV, str(ladder))
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SystemExit) as error:
        serve_command("127.0.0.1", 8001, open_browser=False)
    message = str(error.value)
    assert message.startswith(f"The ladder engine profile {ladder} (arasan) needs arasan-missing")
