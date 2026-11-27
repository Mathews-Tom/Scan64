"""Local, single-process entry point for the built web app and API."""

import asyncio
import shutil
import webbrowser

import uvicorn

from scan64.api.web import web_dist_directory
from scan64.providers.engine.config import (
    DEFAULT_BINARY,
    STOCKFISH_PATH_ENV,
    EngineConfigurationError,
    active_engine_profile,
)


async def _run_server(server: uvicorn.Server, url: str, open_browser: bool) -> None:
    serving = asyncio.create_task(server.serve())
    if open_browser:
        # Do not send the browser to a port that is not listening yet.
        while not server.started and not serving.done():
            await asyncio.sleep(0.05)
        if server.started:
            await asyncio.to_thread(webbrowser.open, url)
    await serving


def _require_engine_binary() -> None:
    """Exit unless the active profile's engine binary exists.

    Only the default profile needs Stockfish. A configured profile needs its
    own binary, and the error names the profile that needs it.
    """
    try:
        profile = active_engine_profile()
    except EngineConfigurationError as error:
        raise SystemExit(f"Engine configuration is invalid: {error}") from error
    if shutil.which(profile.binary_path) is not None:
        return
    if profile.source is not None:
        raise SystemExit(
            f"The engine profile {profile.source} ({profile.name}) needs "
            f"{profile.binary_path}, which is not an executable file or on PATH."
        )
    if profile.binary_path == DEFAULT_BINARY:
        raise SystemExit("Stockfish is required: install stockfish 18+ and put stockfish on PATH.")
    raise SystemExit(
        f"Stockfish is required: {STOCKFISH_PATH_ENV}={profile.binary_path} "
        "is not an executable file or on PATH."
    )


def serve_command(host: str, port: int, *, open_browser: bool) -> None:
    _require_engine_binary()
    if not (web_dist_directory() / "index.html").is_file():
        raise SystemExit(
            "Built web client not found. Run pnpm --dir apps/scan64-web install --frozen-lockfile "
            "and pnpm --dir apps/scan64-web build first."
        )
    browser_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    server = uvicorn.Server(
        uvicorn.Config("scan64.api.app:app", host=host, port=port, timeout_graceful_shutdown=10)
    )
    try:
        asyncio.run(_run_server(server, f"http://{browser_host}:{port}", open_browser))
    except KeyboardInterrupt:
        # Uvicorn re-raises SIGINT after its clean, awaited shutdown.
        pass
