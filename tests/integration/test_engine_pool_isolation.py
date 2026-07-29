import asyncio

import pytest

from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager


@pytest.mark.asyncio
async def test_engine_pool_isolation():
    # Setup pool manager with 1 interactive and 1 batch engine
    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=1, batch_concurrency=1)

    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    try:
        # Start a batch task that takes a while (e.g. 100k nodes)
        batch_task = asyncio.create_task(manager.analyze_batch(fen, nodes=100000))

        # Give it a tiny bit of time to start
        await asyncio.sleep(0.01)

        # Fire an interactive task
        interactive_task = asyncio.create_task(manager.analyze_interactive(fen, nodes=1000))

        # Interactive task should finish quickly, without waiting for the batch task
        interactive_result = await interactive_task
        assert interactive_result is not None
        assert interactive_result.config["nodes"] == 1000

        # Batch task should finish later
        batch_result = await batch_task
        assert batch_result is not None
        assert batch_result.config["nodes"] == 100000

    finally:
        await manager.close()


def test_engine_pool_manager_lifecycle_binds_state_and_closes_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from sqlmodel import create_engine

    import scan64.persistence.database as db_module
    from scan64.api.app import app

    monkeypatch.delenv("SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY", raising=False)
    monkeypatch.delenv("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", raising=False)
    monkeypatch.delenv("SCAN64_ENGINE_POOL_ENABLED", raising=False)
    monkeypatch.setattr(
        db_module,
        "engine",
        create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        ),
    )

    with TestClient(app):
        manager = app.state.engine_pool_manager
        assert isinstance(manager, EnginePoolManager)
        assert manager.interactive_pool.concurrency == 2
        assert manager.batch_pool.concurrency == 2
        assert manager.closed is False

    # TestClient.__exit__ blocks until lifespan shutdown finishes, so this
    # observes the manager after `EnginePoolManager.close()` has run.
    assert manager.closed is True


@pytest.mark.asyncio
async def test_engine_pool_checkout_resets_engine_state_during_its_lifecycle() -> None:
    # Engine reset on checkout: with a single pooled engine, sequential
    # acquisitions must each present a fresh game identity, forcing
    # python-chess to send `ucinewgame` instead of silently reusing state
    # from the previous checkout's analysis or play session.
    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=1, batch_concurrency=1)
    try:
        async with manager.interactive_pool.acquire() as adapter:
            first_token = adapter.game_token
        async with manager.interactive_pool.acquire() as adapter:
            second_token = adapter.game_token
    finally:
        await manager.close()

    assert first_token is not None
    assert second_token is not None
    assert first_token is not second_token
