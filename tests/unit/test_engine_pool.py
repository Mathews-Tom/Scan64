from __future__ import annotations

import asyncio

import pytest

import scan64.providers.stockfish.pool as pool_module
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePool, PooledStockfishAdapter


@pytest.mark.asyncio
async def test_reset_failure_returns_engine_and_next_checkout_resets_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingResetAdapter(PooledStockfishAdapter):
        failed_token: object | None = None

        async def ensure_started(self) -> None:
            pass

        async def reset(self) -> None:
            await super().reset()
            if self.failed_token is None:
                self.failed_token = self.game_token
                raise RuntimeError("reset failed")

    monkeypatch.setattr(pool_module, "PooledStockfishAdapter", FailingResetAdapter)
    pool = EnginePool(StockfishConfig(), concurrency=1)
    try:
        with pytest.raises(RuntimeError, match="reset failed"):
            async with pool.acquire():
                pytest.fail("A failed reset must not yield an engine")

        assert pool._queue.qsize() == pool.concurrency
        async with asyncio.timeout(1):
            async with pool.acquire() as adapter:
                assert isinstance(adapter, FailingResetAdapter)
                assert adapter.game_token is not None
                assert adapter.game_token is not adapter.failed_token
                recovered_adapter = adapter
                recovered_token = adapter.game_token
            async with pool.acquire() as adapter:
                assert adapter is recovered_adapter
                assert adapter.game_token is not recovered_token
        assert pool._queue.qsize() == pool.concurrency
    finally:
        await pool.close()
