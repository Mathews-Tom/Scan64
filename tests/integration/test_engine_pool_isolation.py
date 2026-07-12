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
