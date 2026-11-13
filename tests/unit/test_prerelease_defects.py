"""M73 regressions reproduced against d10eeb6 before applying fixes."""

import asyncio
from unittest.mock import AsyncMock

import chess
import pytest

import scan64.providers.stockfish.pool as pool_module
from scan64.chess.analysis.admission import AdmissionConfig, AdmissionController
from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.insights import insight_step, position_insight
from scan64.learning.principles import principle_facts
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, PooledStockfishAdapter


@pytest.mark.parametrize(
    "fen", ["4k3/8/8/8/8/8/8/4K2R w K - 0 1", "5k2/8/8/8/8/8/8/4K2R w K - 0 1"]
)
@pytest.mark.parametrize("moves", [["e1g1"], ["e1g1", "h1h2"]])
def test_castling_idea_does_not_name_the_answer(moves, fen):
    board = chess.Board(fen)
    insight = position_insight(
        board, EngineAnalysis(raw_result=[{"pv": [move], "score_cp": 0} for move in moves])
    )
    for step in (1, 2):
        serialized = insight_step(board, insight, step).model_dump_json().lower()
        assert "castl" not in serialized
        for candidate in insight.candidates:
            assert candidate.move not in serialized
            assert candidate.san.lower() not in serialized
            assert candidate.move[2:4] not in serialized
    idea = insight_step(board, insight, 2)
    assert idea.motif == "king safety"
    assert idea.targets == []


@pytest.mark.asyncio
async def test_identical_hints_reuse_engine_result(monkeypatch):
    calls = 0

    async def analyze(*args, **kwargs):
        nonlocal calls
        calls += 1
        return EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 0}])

    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    manager = EnginePoolManager(StockfishConfig())
    try:
        first = await manager.analyze_hint(chess.STARTING_FEN)
        second = await manager.analyze_hint(chess.STARTING_FEN)
        assert first.raw_result == second.raw_result
        assert calls == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("quota", [0, 1])
async def test_shutdown_cancels_running_and_queued_analysis(quota):
    controller = AdmissionController(AdmissionConfig(daily_quota_games=quota))
    started = 0
    all_started = asyncio.Event()
    cancelled = []
    running_tasks = []

    async def analyze():
        nonlocal started
        task = asyncio.current_task()
        running_tasks.append(task)
        started += 1
        if started == quota + 1:
            all_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(task)

    running = controller.submit("player", analyze)
    queued = controller.submit("player", analyze)
    waiting = controller.submit("player", analyze)
    await asyncio.wait_for(all_started.wait(), 1)
    try:
        await controller.stop()
        assert len(cancelled) == quota + 1
        assert all(task.done() for task in running_tasks)
        assert running.cancelled()
        assert queued.cancelled()
        assert waiting.cancelled()
        assert not controller.active_players
        assert not controller.queues
    finally:
        # Baseline cleanup must not leave the reproduced orphan alive in pytest.
        for task in running_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*running_tasks, return_exceptions=True)
        await controller.stop()


@pytest.mark.parametrize(
    ("fen", "applies"),
    [
        ("r6k/8/8/8/8/8/5PRP/6K1 w - - 0 1", True),
        ("7k/8/q7/8/8/8/3n1PRP/6K1 w - - 0 1", False),
    ],
    ids=["escape-defeats-rook-mate", "check-only-masks-queen-mate"],
)
@pytest.mark.parametrize("mirror", [False, True])
def test_check_does_not_disguise_a_surviving_back_rank_mate(fen, applies, mirror):
    board = chess.Board(fen)
    move = chess.Move.from_uci("g2g8")
    if mirror:
        board = board.mirror()
        move = chess.Move(
            chess.square_mirror(move.from_square), chess.square_mirror(move.to_square)
        )
    assert board.is_valid()
    assert move in board.legal_moves
    after = board.copy()
    after.push(move)
    assert after.is_check()
    facts = principle_facts(board, move, after)
    assert any(kind == "king_escape" for kind, _, _ in facts) == applies


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("binary_path", "another-stockfish"),
        ("threads", 2),
        ("hash_size", 32),
        ("HINT_NODES", 100_000),
        ("HINT_TIME_MS", 500),
        ("HINT_MULTIPV", 2),
    ],
)
async def test_hint_cache_keys_every_engine_input(monkeypatch, setting, value):
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 0}]))
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    manager = EnginePoolManager(StockfishConfig())
    try:
        await manager.analyze_hint(chess.STARTING_FEN)
        if setting.startswith("HINT_"):
            monkeypatch.setattr(pool_module, setting, value)
        else:
            monkeypatch.setattr(manager.config, setting, value)
        await manager.analyze_hint(chess.STARTING_FEN)
        assert analyze.await_count == 2
        await manager.analyze_hint(chess.STARTING_FEN)
        assert analyze.await_count == 2
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_hint_cache_is_bounded_lru_and_returns_independent_results(monkeypatch):
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 0}]))
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    monkeypatch.setattr(pool_module, "HINT_CACHE_SIZE", 2)
    manager = EnginePoolManager(StockfishConfig())
    fens = [chess.STARTING_FEN.replace("0 1", f"{clock} 1") for clock in range(3)]
    try:
        first = await manager.analyze_hint(fens[0])
        first.raw_result[0]["pv"] = ["a2a4"]
        await manager.analyze_hint(fens[1])
        cached = await manager.analyze_hint(fens[0])
        assert cached.raw_result[0]["pv"] == ["e2e4"]
        cached.raw_result[0]["pv"] = ["h2h4"]
        await manager.analyze_hint(fens[2])
        assert len(manager._hint_cache) == 2
        assert (await manager.analyze_hint(fens[0])).raw_result[0]["pv"] == ["e2e4"]
        assert analyze.await_count == 3
        await manager.analyze_hint(fens[1])
        assert analyze.await_count == 4
    finally:
        await manager.close()
    assert not manager._hint_cache


@pytest.mark.asyncio
async def test_failed_hint_is_not_cached(monkeypatch):
    analyze = AsyncMock(
        side_effect=[OSError("unavailable"), EngineAnalysis(raw_result=[{"pv": ["e2e4"]}])]
    )
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    manager = EnginePoolManager(StockfishConfig())
    try:
        with pytest.raises(OSError):
            await manager.analyze_hint(chess.STARTING_FEN)
        await manager.analyze_hint(chess.STARTING_FEN)
        assert analyze.await_count == 2
    finally:
        await manager.close()
