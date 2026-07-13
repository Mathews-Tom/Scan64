import chess
import pytest

from scan64.chess.opponents.protocols import OpponentContext
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.chess.positions.models import Position
from scan64.providers.stockfish.adapter import StockfishConfig

pytestmark = pytest.mark.asyncio

async def test_stockfish_opponent_provider_unit():
    provider = StockfishOpponentProvider(StockfishConfig())
    position = Position(fen=chess.STARTING_FEN, side_to_move="w", canonical_id="start")

    context_weak = OpponentContext(strength_setting=0)
    decision_weak = await provider.choose_move(position, context_weak)
    assert decision_weak.uci_move != "0000"

    context_strong = OpponentContext(strength_setting=20)
    decision_strong = await provider.choose_move(position, context_strong)
    assert decision_strong.uci_move != "0000"

async def test_stockfish_move_quality_differential():
    provider = StockfishOpponentProvider(StockfishConfig())

    # Complex middle game where weak engine will make a sub-optimal move
    # White to play. Mate in 5.
    fen = "3r4/pR2N3/2pkb3/5p2/8/2B5/qP3PPP/4R1K1 w - - 1 1"
    position = Position(fen=fen, side_to_move="w", canonical_id="complex")

    # We will try a few times if they happen to be same, since Stockfish skill=0 is randomish
    diff_found = False
    for _ in range(5):
        weak_context = OpponentContext(strength_setting=0, time_remaining_ms=10)
        decision_weak = await provider.choose_move(position, weak_context)

        strong_context = OpponentContext(strength_setting=20, time_remaining_ms=500)
        decision_strong = await provider.choose_move(position, strong_context)

        if decision_weak.uci_move != decision_strong.uci_move:
            _, engine = await chess.engine.popen_uci("stockfish")
            board = chess.Board(fen)

            board.push_uci(decision_weak.uci_move)
            info_weak = await engine.analyse(board, chess.engine.Limit(depth=12))
            score_weak = -info_weak["score"].pov(board.turn).score(mate_score=10000)
            board.pop()

            board.push_uci(decision_strong.uci_move)
            info_strong = await engine.analyse(board, chess.engine.Limit(depth=12))
            score_strong = -info_strong["score"].pov(board.turn).score(mate_score=10000)
            board.pop()

            await engine.quit()

            if score_strong > score_weak:
                diff_found = True
                break

    assert diff_found, "Strong engine should find a measurably better move than weak engine"

