from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, select

from scan64.chess.analysis import jobs
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.games.models import Game
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.plugins.registry import PluginRegistry
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, PooledStockfishAdapter


@pytest.mark.asyncio
async def test_failed_pooled_search_returns_engines_and_leaves_no_partial_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    occupied = asyncio.Event()
    active = 0

    async def start_without_process(self: PooledStockfishAdapter) -> None:
        pass

    async def fail_search(
        self: PooledStockfishAdapter, fen: str, **limits: int | None
    ) -> EngineAnalysis:
        nonlocal active
        active += 1
        if active == 2:
            occupied.set()
        try:
            await asyncio.wait_for(occupied.wait(), timeout=2)
            if " w " in fen:
                raise RuntimeError("pooled search failed")
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        finally:
            await asyncio.sleep(0)
            active -= 1

    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", start_without_process)
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", fail_search)
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    monkeypatch.setattr(jobs, "get_host_registry", lambda: registry)
    game = Game(
        pgn="", moves=["e2e4", "e7e5"], owner_color="white", owner_player_id="failure-player"
    )
    db_session.add(game)
    db_session.commit()
    job = AnalysisJob(game_id=game.id)
    db_session.add(job)
    db_session.commit()
    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=1, batch_concurrency=2)
    try:
        await jobs.execute_analysis_job_async(job.id, manager)
        db_session.refresh(job)
        assert job.status == "failed"
        assert job.error == "pooled search failed"
        assert active == 0
        assert manager.batch_pool._queue.qsize() == 2
        for model in (EngineAnalysis, GameMoveReview, PersistedLessonOpportunity):
            assert db_session.exec(select(model)).all() == []
    finally:
        await manager.close()
