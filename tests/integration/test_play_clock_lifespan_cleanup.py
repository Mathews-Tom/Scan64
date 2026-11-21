from __future__ import annotations

import asyncio
import threading
from datetime import datetime

import chess
import pytest
from sqlmodel import Session

import scan64.api.app as app_module
from scan64.chess.games.clocks import PlayClockRuntime
from scan64.learning.plugins.host_registry import clear_host_registry, get_host_registry
from scan64.providers.stockfish.pool import EnginePoolManager


@pytest.mark.asyncio
async def test_cancelled_clock_drain_still_closes_lifespan_dependencies(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN64_ENGINE_POOL_ENABLED", "1")
    monkeypatch.setenv("SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY", "1")
    monkeypatch.setenv("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", "1")
    monkeypatch.setattr(app_module, "shell_token", None)
    entered = threading.Event()
    release = threading.Event()
    original_check = PlayClockRuntime._check_deadline

    def blocked_check(runtime: PlayClockRuntime) -> datetime | None:
        entered.set()
        if not release.wait(20):
            raise TimeoutError("Test did not release the deadline worker")
        return original_check(runtime)

    monkeypatch.setattr(PlayClockRuntime, "_check_deadline", blocked_check)
    context = app_module.lifespan(app_module.app)
    await context.__aenter__()
    pool: EnginePoolManager = app_module.app.state.engine_pool_manager
    runtime: PlayClockRuntime = app_module.app.state.play_clock_runtime
    shutdown: asyncio.Task[bool | None] | None = None
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), timeout=6)
        # Open a real Stockfish process so skipping cleanup leaks an owned resource.
        await asyncio.wait_for(pool.analyze_interactive(chess.STARTING_FEN, nodes=64), timeout=5)
        assert not pool.closed
        assert get_host_registry() is app_module.app.state.plugin_registry
        stop_entered = asyncio.Event()
        original_stop = runtime.stop

        async def observed_stop() -> None:
            stop_entered.set()
            await original_stop()

        monkeypatch.setattr(runtime, "stop", observed_stop)
        shutdown = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.wait_for(stop_entered.wait(), timeout=2)
        shutdown.cancel()
        assert not shutdown.done()
        assert not pool.closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(shutdown), timeout=5)
        assert pool.closed
        with pytest.raises(RuntimeError, match="not initialized"):
            get_host_registry()
    finally:
        release.set()
        if shutdown is None:
            await asyncio.wait_for(context.__aexit__(None, None, None), timeout=5)
        else:
            await asyncio.wait_for(asyncio.gather(shutdown, return_exceptions=True), timeout=5)
        await pool.close()
        await app_module.admission_controller.stop()
        clear_host_registry()
