import pytest

from src.scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig


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
