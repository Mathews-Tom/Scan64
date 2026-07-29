import asyncio

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

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


@pytest.mark.asyncio
async def test_opponent_provider_reuses_the_pooled_engine_across_moves() -> None:
    from scan64.chess.opponents.protocols import OpponentContext
    from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
    from scan64.chess.positions.models import Position

    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=1, batch_concurrency=1)
    provider = StockfishOpponentProvider(StockfishConfig(), pool_manager=manager)
    position = Position(
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
    )
    context = OpponentContext(strength_setting=1, time_remaining_ms=None)

    try:
        decision = await provider.choose_move(position, context)
        assert decision.uci_move != "0000"
        async with manager.interactive_pool.acquire() as adapter:
            first_engine = adapter._engine

        decision_again = await provider.choose_move(position, context)
        assert decision_again.uci_move != "0000"
        async with manager.interactive_pool.acquire() as adapter:
            second_engine = adapter._engine

        # interactive_concurrency=1 means both moves could only have been
        # served by spawning a second process if the provider bypassed the
        # pool; the same process serving both moves is the bound this
        # milestone's acceptance requires ("process count stays bounded
        # under concurrent play plus analysis").
        assert first_engine is not None
        assert second_engine is first_engine
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_analysis_job_runs_on_the_batch_pool_in_isolation_from_interactive_play(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scan64.chess.analysis.jobs as jobs
    from scan64.chess.games.models import Game
    from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
    from scan64.learning.plugins.registry import PluginRegistry
    from scan64.providers.stockfish.pool import PoolBoundAdapter

    captured_adapters: list[object] = []

    class _CapturingOrchestrator:
        def __init__(self, adapter: object, *_: object) -> None:
            captured_adapters.append(adapter)

        async def run_fast_pass(
            self, _: list[str], initial_fen: str | None = None
        ) -> list[object]:
            return []

        async def run_focused_pass(self, candidates: list[object]) -> list[object]:
            return []

    monkeypatch.setattr(jobs, "FastPassOrchestrator", _CapturingOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _CapturingOrchestrator)

    game = Game(pgn="", moves=["e2e4"], owner_player_id="pool-analysis-player")

    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=1, batch_concurrency=1)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            registry = PluginRegistry()
            register_seeded_detectors(registry)
            await jobs.run_analysis_for_game(
                game, session, registry=registry, pool_manager=manager
            )

        # Both orchestrator passes were built from the batch pool, not from a
        # per-call adapter or the interactive pool.
        assert len(captured_adapters) == 2
        assert all(isinstance(adapter, PoolBoundAdapter) for adapter in captured_adapters)
        assert all(adapter._pool is manager.batch_pool for adapter in captured_adapters)

        # Isolation: routing analysis through the batch pool never touches
        # the interactive pool, matching this milestone's acceptance that a
        # move request completes independent of any running batch analysis.
        assert manager.interactive_pool._initialized is False
    finally:
        await manager.close()
