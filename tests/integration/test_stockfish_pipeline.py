import pytest

from scan64.chess.analysis.orchestration import (
    FastPassConfig,
    FastPassOrchestrator,
    FocusedPassConfig,
    FocusedPassOrchestrator,
)
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig


@pytest.mark.asyncio
async def test_stockfish_fast_pass():
    adapter = StockfishAdapter(StockfishConfig())
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    # 1000 nodes is very fast
    result = await adapter.analyze_position(fen, nodes=1000)

    assert result.config["engine_name"].startswith("Stockfish")
    assert result.config["nodes"] == 1000
    assert len(result.raw_result) == 1
    assert "score_cp" in result.raw_result[0] or "score_mate" in result.raw_result[0]


@pytest.mark.asyncio
async def test_stockfish_focused_pass():
    adapter = StockfishAdapter(StockfishConfig())
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    result = await adapter.analyze_position(fen, nodes=10000, multipv=4)

    assert result.config["multipv"] == 4
    # Note: Sometimes Stockfish may return fewer PVs if the position is very simple,
    # but for starting position it should return 4 at 10k nodes.
    assert len(result.raw_result) > 1


@pytest.mark.asyncio
async def test_fast_pass_orchestrator():
    adapter = StockfishAdapter(StockfishConfig())
    orchestrator = FastPassOrchestrator(
        adapter, FastPassConfig(nodes=10000, swing_threshold_cp=100)
    )

    # 1. e4 e5 2. Nf3 Nc6 3. Bc4 Nd4 (a mistake, gives up e5 and drops evaluation)
    moves = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nd4"]

    candidates = await orchestrator.run_fast_pass(moves)

    assert len(candidates) >= 1
    # Nd4 is the 6th move (index 5)
    assert candidates[-1].move_index == 5
    assert candidates[-1].swing_cp >= 100


@pytest.mark.asyncio
async def test_focused_pass_orchestrator():
    adapter = StockfishAdapter(StockfishConfig())
    fast_orchestrator = FastPassOrchestrator(
        adapter, FastPassConfig(nodes=10000, swing_threshold_cp=100)
    )
    focused_orchestrator = FocusedPassOrchestrator(
        adapter, FocusedPassConfig(nodes=50000, multipv=4)
    )

    moves = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nd4"]
    candidates = await fast_orchestrator.run_fast_pass(moves)

    # Nd4 is the mistake
    assert len(candidates) >= 1

    focused_results = await focused_orchestrator.run_focused_pass(candidates)

    assert len(focused_results) == len(candidates)
    assert focused_results[-1].config["multipv"] == 4
    assert len(focused_results[-1].raw_result) > 1
