"""Local, single-process entry point for the built web app and API."""

import asyncio
import shutil
import webbrowser

import uvicorn

from scan64.api.web import web_dist_directory


async def _run_server(server: uvicorn.Server, url: str, open_browser: bool) -> None:
    serving = asyncio.create_task(server.serve())
    if open_browser:
        # Do not send the browser to a port that is not listening yet.
        while not server.started and not serving.done():
            await asyncio.sleep(0.05)
        if server.started:
            await asyncio.to_thread(webbrowser.open, url)
    await serving


def serve_command(host: str, port: int, *, open_browser: bool) -> None:
    if shutil.which("stockfish") is None:
        raise SystemExit("Stockfish is required: install stockfish 18+ and put stockfish on PATH.")
    if not (web_dist_directory() / "index.html").is_file():
        raise SystemExit(
            "Built web client not found. Run pnpm --dir apps/scan64-web install --frozen-lockfile "
            "and pnpm --dir apps/scan64-web build first."
        )
    browser_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    server = uvicorn.Server(uvicorn.Config("scan64.api.app:app", host=host, port=port))
    try:
        asyncio.run(_run_server(server, f"http://{browser_host}:{port}", open_browser))
    except KeyboardInterrupt:
        # Uvicorn re-raises SIGINT after its clean, awaited shutdown.
        pass
