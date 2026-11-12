import asyncio
from unittest.mock import AsyncMock

import chess
import chess.engine
import pytest

from scan64.chess.analysis.models import EngineAnalysis
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, PooledStockfishAdapter


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    return EnginePoolManager(StockfishConfig())


@pytest.mark.asyncio
async def test_hint_reserves_interactive_opponent_capacity(manager, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    result = EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 0}])

    async def analyze(*args, **kwargs):
        assert kwargs == {"nodes": 200_000, "time_ms": 1500, "multipv": 4}
        started.set()
        await release.wait()
        return result

    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    move = chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None)
    play = AsyncMock(return_value=move)
    monkeypatch.setattr(PooledStockfishAdapter, "play_move", play)
    task = asyncio.create_task(manager.analyze_hint(chess.STARTING_FEN))
    await started.wait()
    try:
        with pytest.raises(TimeoutError, match="capacity"):
            await manager.analyze_hint(chess.STARTING_FEN)
        assert (
            await asyncio.wait_for(
                manager.play_interactive(chess.STARTING_FEN, 0, chess.engine.Limit(nodes=1)), 0.1
            )
            is move
        )
        assert not manager.batch_pool._initialized
    finally:
        release.set()
        assert await task is result
    assert manager.interactive_pool._queue.qsize() == 2


@pytest.mark.asyncio
async def test_hint_acquire_is_bounded_and_retryable(manager):
    async with manager.interactive_pool.acquire():
        async with manager.interactive_pool.acquire():
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(manager.analyze_hint(chess.STARTING_FEN), 0.5)
    assert not manager._hint_in_progress
    assert manager.interactive_pool._queue.qsize() == 2


@pytest.mark.asyncio
async def test_cancelled_hint_returns_slot(manager, monkeypatch):
    started = asyncio.Event()

    async def analyze(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    task = asyncio.create_task(manager.analyze_hint(chess.STARTING_FEN))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manager._hint_in_progress
    assert manager.interactive_pool._queue.qsize() == 2


@pytest.mark.asyncio
async def test_search_wall_timeout_returns_slot(manager, monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", slow)
    with pytest.raises(TimeoutError):
        await manager.analyze_hint(chess.STARTING_FEN)
    assert not manager._hint_in_progress
    assert manager.interactive_pool._queue.qsize() == 2


@pytest.mark.asyncio
async def test_single_engine_rejects_hints_without_starting():
    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=1)
    with pytest.raises(TimeoutError, match="capacity"):
        await manager.analyze_hint(chess.STARTING_FEN)
    assert not manager.interactive_pool._initialized


@pytest.mark.asyncio
async def test_cold_checkouts_initialize_only_one_pool(manager, monkeypatch):
    started = 0

    async def start(adapter):
        nonlocal started
        started += 1
        await asyncio.sleep(0.01)

    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", start)
    await asyncio.gather(*(manager.interactive_pool.initialize() for _ in range(10)))
    assert started == 2
    assert manager.interactive_pool._queue.qsize() == 2


@pytest.mark.asyncio
async def test_cancelled_initialization_closes_partial_pool_and_can_retry(manager, monkeypatch):
    started = asyncio.Event()
    quit_engine = AsyncMock()

    async def start(adapter):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", start)
    monkeypatch.setattr(PooledStockfishAdapter, "quit", quit_engine)
    task = asyncio.create_task(manager.interactive_pool.initialize())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    quit_engine.assert_awaited_once()
    assert manager.interactive_pool._queue.empty()
    assert not manager.interactive_pool._initialized
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    await manager.interactive_pool.initialize()
    assert manager.interactive_pool._queue.qsize() == 2
