from __future__ import annotations

import asyncio

import chess
import pytest

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import (
    CandidatePosition,
    FastPassConfig,
    FastPassOrchestrator,
    FocusedPassConfig,
    FocusedPassOrchestrator,
)
from scan64.providers.stockfish import pool
from scan64.providers.stockfish.adapter import StockfishConfig

MOVES = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nd4"]


def positions() -> list[str]:
    board = chess.Board()
    fens = [board.fen()]
    for move in MOVES:
        board.push_san(move)
        fens.append(board.fen())
    return fens


@pytest.mark.parametrize(
    "cpus,interactive,expected",
    [(1, 2, 2), (4, 2, 2), (12, 2, 9), (12, 4, 7), (None, 2, 2)],
)
def test_batch_capacity_reserves_interactive_and_application_cpus(
    monkeypatch: pytest.MonkeyPatch, cpus: int | None, interactive: int, expected: int
) -> None:
    monkeypatch.setattr(pool.os, "cpu_count", lambda: cpus)
    monkeypatch.delattr(pool.os, "sched_getaffinity", raising=False)
    monkeypatch.setenv("SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY", str(interactive))
    monkeypatch.delenv("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", raising=False)
    manager = pool.EnginePoolManager.from_env(StockfishConfig())
    assert manager.batch_pool.concurrency == expected
    assert manager.interactive_pool.concurrency == interactive


def test_batch_capacity_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pool.os, "cpu_count", lambda: 12)
    monkeypatch.setenv("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", "3")
    assert pool.EnginePoolManager.from_env(StockfishConfig()).batch_pool.concurrency == 3


def test_batch_capacity_uses_process_affinity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pool.os, "cpu_count", lambda: 64)
    monkeypatch.setattr(pool.os, "sched_getaffinity", lambda _: set(range(8)), raising=False)
    monkeypatch.setenv("SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY", "2")
    monkeypatch.delenv("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", raising=False)
    assert pool.EnginePoolManager.from_env(StockfishConfig()).batch_pool.concurrency == 5
    monkeypatch.setenv("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", "3")
    assert pool.EnginePoolManager.from_env(StockfishConfig()).batch_pool.concurrency == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("focused", [False, True])
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_passes_bound_searches_and_preserve_position_order(
    focused: bool, concurrency: int
) -> None:
    fens = positions()
    active = 0
    peak = 0
    started: list[str] = []
    completed: list[str] = []
    occupied = asyncio.Event()
    release = asyncio.Event()
    second_finished = asyncio.Event()

    class Adapter:
        async def analyze_position(
            self,
            fen: str,
            nodes: int | None = None,
            depth: int | None = None,
            time_ms: int | None = None,
            multipv: int = 1,
        ) -> EngineAnalysis:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.append(fen)
            if active == concurrency:
                occupied.set()
            try:
                await release.wait()
                if concurrency == 2 and fen == fens[0]:
                    await second_finished.wait()
                if fen == fens[1]:
                    second_finished.set()
                completed.append(fen)
                return EngineAnalysis(
                    position_id=None,
                    config={"fen": fen, "nodes": nodes, "multipv": multipv},
                    raw_result=[{"score_cp": 0}],
                )
            finally:
                active -= 1

    adapter = Adapter()

    async def run_pass() -> list[EngineAnalysis]:
        if focused:
            blank = EngineAnalysis(position_id=None, raw_result=[])
            candidates = [
                CandidatePosition(fen, i, blank, blank, 200) for i, fen in enumerate(fens)
            ]
            return await FocusedPassOrchestrator(
                adapter, FocusedPassConfig(nodes=1000, concurrency=concurrency)
            ).run_focused_pass(candidates)
        result = await FastPassOrchestrator(
            adapter, FastPassConfig(nodes=1000, concurrency=concurrency)
        ).run_fast_pass(MOVES)
        return result.evaluations

    task = asyncio.create_task(run_pass())
    try:
        await asyncio.wait_for(occupied.wait(), timeout=2)
        assert len(started) == concurrency
        release.set()
        analyses = await asyncio.wait_for(task, timeout=2)
        assert [item.config["fen"] for item in analyses] == fens
        assert all(item.config["multipv"] == (4 if focused else 1) for item in analyses)
        assert peak == concurrency
        assert active == 0
        if concurrency == 2:
            assert completed.index(fens[1]) < completed.index(fens[0])
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_pass_failure_awaits_sibling_cleanup(cancel: bool) -> None:
    started = 0
    active = 0
    occupied = asyncio.Event()
    fail = asyncio.Event()
    cleaned: list[str] = []

    class Adapter:
        async def analyze_position(
            self,
            fen: str,
            nodes: int | None = None,
            depth: int | None = None,
            time_ms: int | None = None,
            multipv: int = 1,
        ) -> EngineAnalysis:
            nonlocal started, active
            started += 1
            active += 1
            if started == 2:
                occupied.set()
            try:
                if fen == chess.STARTING_FEN:
                    await fail.wait()
                    raise RuntimeError("engine failed")
                await asyncio.Event().wait()
                raise AssertionError("unreachable")
            finally:
                await asyncio.sleep(0)
                active -= 1
                cleaned.append(fen)

    task = asyncio.create_task(
        FastPassOrchestrator(Adapter(), FastPassConfig(concurrency=2)).run_fast_pass(MOVES)
    )
    try:
        await asyncio.wait_for(occupied.wait(), timeout=2)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            fail.set()
            with pytest.raises(RuntimeError, match="engine failed"):
                await task
        assert active == 0
        assert set(cleaned) == set(positions()[:2])
        assert started == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_pool_retains_engines_when_startup_rollback_is_cancelled_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: set[pool.PooledStockfishAdapter] = set()
    cold_start = asyncio.Event()
    cleaning = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def start(adapter: pool.PooledStockfishAdapter) -> None:
        started.add(adapter)
        if len(started) == 2:
            cold_start.set()
            await asyncio.Event().wait()

    async def quit(adapter: pool.PooledStockfishAdapter) -> None:
        cleaning.set()
        await release_cleanup.wait()
        started.discard(adapter)

    monkeypatch.setattr(pool.PooledStockfishAdapter, "ensure_started", start)
    monkeypatch.setattr(pool.PooledStockfishAdapter, "quit", quit)
    manager = pool.EnginePoolManager(
        StockfishConfig(), interactive_concurrency=1, batch_concurrency=2
    )
    task = asyncio.create_task(
        FastPassOrchestrator(manager.batch_adapter, FastPassConfig(concurrency=2)).run_fast_pass(
            MOVES
        )
    )
    try:
        await asyncio.wait_for(cold_start.wait(), timeout=2)
        task.cancel()
        await asyncio.wait_for(cleaning.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release_cleanup.set()
        await manager.close()
        assert not started
    finally:
        release_cleanup.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.close()
