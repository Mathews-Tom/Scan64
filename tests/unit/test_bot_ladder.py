from unittest.mock import AsyncMock

import chess
import chess.engine
import pytest

from scan64.chess.opponents.bot_policy import (
    DEFAULT_BOT_NODES,
    choose_ladder_move,
    error_budget,
    sample_candidate,
)
from scan64.chess.opponents.bots import BOT_LEVELS, get_bot_level
from scan64.chess.opponents.protocols import OpponentContext
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.chess.positions.models import Position
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, PooledStockfishAdapter


def candidates() -> list[chess.engine.InfoDict]:
    return [
        {
            "pv": [chess.Move.from_uci(move)],
            "score": chess.engine.PovScore(chess.engine.Cp(score), chess.WHITE),
        }
        for move, score in (("e2e4", 100), ("d2d4", 70), ("g1f3", -100), ("b1c3", -500))
    ]


def test_catalogue_is_single_immutable_eight_level_definition() -> None:
    assert isinstance(BOT_LEVELS, tuple)
    assert [bot.level for bot in BOT_LEVELS] == list(range(1, 9))
    assert all(bot.name and bot.description for bot in BOT_LEVELS)
    assert all(get_bot_level(bot.level) is bot for bot in BOT_LEVELS)
    budgets = [error_budget(bot.level) for bot in BOT_LEVELS]
    assert budgets == sorted(budgets, reverse=True)
    assert len(set(budgets)) == 8
    assert budgets[-1] == 0


@pytest.mark.parametrize("level", [-1, 0, 9, 100])
def test_unknown_bot_level_is_rejected(level: int) -> None:
    with pytest.raises(ValueError, match="Unknown bot level"):
        get_bot_level(level)


def test_context_keeps_legacy_constructor() -> None:
    context = OpponentContext(1500, 50)
    assert context.strength_setting == 1500
    assert context.time_remaining_ms == 50
    assert (context.bot_level, context.seed, context.ply, context.nodes) == (None, 0, 0, None)


def test_sampler_is_seeded_order_independent_and_within_budget() -> None:
    board = chess.Board()
    infos = candidates()
    for level in range(1, 9):
        for seed in range(20):
            context = OpponentContext(0, bot_level=level, seed=seed, ply=4)
            first = sample_candidate(board, infos, context)
            repeat = sample_candidate(board, list(reversed(infos)), context)
            assert first.move == repeat.move
            assert first.move in board.legal_moves
            assert 100 - first.info["score"].white().score() <= error_budget(level)
            if level == 8:
                assert first.move == chess.Move.from_uci("e2e4")
    moves = {
        sample_candidate(board, infos, OpponentContext(0, bot_level=1, seed=seed)).move
        for seed in range(20)
    }
    assert len(moves) > 1


def test_sampler_handles_black_pov_and_mate() -> None:
    board = chess.Board()
    board.push_uci("f2f3")
    board.push_uci("e7e5")
    board.push_uci("g2g4")
    infos: list[chess.engine.InfoDict] = [
        {
            "pv": [chess.Move.from_uci("d8h4")],
            "score": chess.engine.PovScore(chess.engine.Mate(1), chess.BLACK),
        },
        {
            "pv": [chess.Move.from_uci("b8c6")],
            "score": chess.engine.PovScore(chess.engine.Cp(200), chess.BLACK),
        },
    ]
    for level in range(1, 9):
        result = sample_candidate(board, infos, OpponentContext(0, bot_level=level))
        assert result.move == chess.Move.from_uci("d8h4")


def test_sampler_refuses_missing_or_illegal_candidates() -> None:
    infos: list[chess.engine.InfoDict] = [
        {
            "pv": [chess.Move.from_uci("e7e5")],
            "score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
        },
        {"pv": [chess.Move.from_uci("e2e4")]},
    ]
    with pytest.raises(chess.engine.EngineError, match="no scored legal"):
        sample_candidate(chess.Board(), infos, OpponentContext(0, bot_level=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [1, 5, 8])
async def test_ladder_search_uses_full_strength_and_fixed_nodes(
    level: int,
) -> None:
    engine = AsyncMock()
    engine.analyse.return_value = candidates()
    token = object()
    result = await choose_ladder_move(
        engine, chess.Board(), OpponentContext(0, bot_level=level, nodes=3210), token
    )
    assert result.move in chess.Board().legal_moves
    engine.configure.assert_awaited_once_with(
        {"Threads": 1, "Skill Level": 20, "UCI_LimitStrength": False, "UCI_Elo": 1320}
    )
    call = engine.analyse.await_args
    assert call.args[1].nodes == 3210
    assert call.args[1].time is None
    assert call.kwargs["multipv"] == chess.Board().legal_moves.count()
    assert call.kwargs["game"] is token
    engine.play.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_nodes_match_production_and_calibration() -> None:
    engine = AsyncMock()
    engine.analyse.return_value = candidates()
    await choose_ladder_move(engine, chess.Board(), OpponentContext(0, bot_level=1), object())
    assert engine.analyse.await_args.args[1].nodes == DEFAULT_BOT_NODES
    with pytest.raises(ValueError, match="positive"):
        await choose_ladder_move(
            engine, chess.Board(), OpponentContext(0, bot_level=1, nodes=0), object()
        )


@pytest.mark.asyncio
async def test_pool_resets_strength_for_analysis_and_legacy_play(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = AsyncMock()
    engine.id = {"name": "Stockfish test"}
    engine.analyse.return_value = candidates()
    engine.play.return_value = chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None)
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    adapter = PooledStockfishAdapter(StockfishConfig(threads=2))
    try:
        await adapter.reset()
        first_token = adapter.game_token
        await adapter.play_bot_move(chess.STARTING_FEN, OpponentContext(0, bot_level=6))
        await adapter.reset()
        assert adapter.game_token is not first_token
        await adapter.analyze_position(chess.STARTING_FEN, nodes=123)
        assert engine.configure.await_args.args[0] == {
            "Threads": 2,
            "Skill Level": 20,
            "UCI_LimitStrength": False,
            "UCI_Elo": 1320,
        }
        await adapter.play_move(chess.STARTING_FEN, 4, chess.engine.Limit(nodes=123))
        assert engine.configure.await_args.args[0] == {
            "Threads": 2,
            "Skill Level": 4,
            "UCI_LimitStrength": False,
            "UCI_Elo": 1320,
        }
    finally:
        await adapter.quit()


@pytest.mark.asyncio
@pytest.mark.parametrize("stored, expected", [(-50, 0), (1500, 20), (7, 7)])
async def test_legacy_strength_is_clamped_for_resume(
    monkeypatch: pytest.MonkeyPatch, stored: int, expected: int
) -> None:
    engine = AsyncMock()
    engine.play.return_value = chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None)
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    provider = StockfishOpponentProvider(StockfishConfig())
    await provider.choose_move(
        Position(fen=chess.STARTING_FEN, side_to_move="w", canonical_id="start"),
        OpponentContext(stored),
    )
    assert engine.configure.await_args.args[0]["Skill Level"] == expected
    assert engine.configure.await_args.args[0]["UCI_LimitStrength"] is False
    engine.quit.assert_awaited_once()


@pytest.mark.asyncio
async def test_pooled_and_standalone_use_identical_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engines: list[AsyncMock] = []

    async def start_engine(path: str) -> tuple[None, AsyncMock]:
        engine = AsyncMock()
        engine.analyse.return_value = candidates()
        engines.append(engine)
        return None, engine

    monkeypatch.setattr(chess.engine, "popen_uci", start_engine)
    config = StockfishConfig()
    manager = EnginePoolManager(config, interactive_concurrency=1, batch_concurrency=1)
    position = Position(fen=chess.STARTING_FEN, side_to_move="w", canonical_id="start")
    context = OpponentContext(0, bot_level=2, seed=71, ply=0)
    try:
        standalone = await StockfishOpponentProvider(config).choose_move(position, context)
        pooled = await StockfishOpponentProvider(config, manager).choose_move(position, context)
        assert standalone.uci_move == pooled.uci_move
        assert len(engines) == 2
        for engine in engines:
            assert engine.analyse.await_args.args[1].nodes == DEFAULT_BOT_NODES
    finally:
        await manager.close()
