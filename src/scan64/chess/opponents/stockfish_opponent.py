import time

import chess
import chess.engine

from scan64.chess.opponents.protocols import MoveDecision, OpponentContext, OpponentPolicy
from scan64.chess.positions.models import Position
from scan64.providers.stockfish.adapter import StockfishConfig


class StockfishOpponentProvider(OpponentPolicy):
    def __init__(self, config: StockfishConfig):
        self.config = config

    async def choose_move(self, position: Position, context: OpponentContext) -> MoveDecision:
        """
        Choose a move for the given position using Stockfish.

        The context.strength_setting maps to Stockfish's Skill Level (0-20).
        This provides an approximate Elo range of 1320 - 3190.
        Note: The playstyle at reduced strength is NOT human-like. It relies on
        occasional blunders interspersed with strong moves, and is only meant
        for configurable conventional engine play prior to Maia integration (M21).
        """
        board = chess.Board(position.fen)
        _, engine = await chess.engine.popen_uci(self.config.binary_path)
        try:
            skill_level = max(0, min(20, context.strength_setting))
            await engine.configure({
                "Threads": self.config.threads,
                "Hash": self.config.hash_size,
                "Skill Level": skill_level,
            })

            # Stockfish Skill Level works better with depth limits too, but time is fine.
            # We'll use a fast limit so tests don't take forever.
            limit = chess.engine.Limit(time=0.1)
            if context.time_remaining_ms:
                limit = chess.engine.Limit(time=context.time_remaining_ms / 1000.0)

            start_time = time.monotonic()
            result = await engine.play(board, limit, info=chess.engine.INFO_SCORE)
            end_time = time.monotonic()

            time_taken_ms = int((end_time - start_time) * 1000)

            score = None
            if result.info and "score" in result.info:
                pov_score = result.info["score"].pov(board.turn)
                if pov_score.is_mate():
                    # High arbitrary value for mate
                    score = 10000 if pov_score.mate() > 0 else -10000
                else:
                    score = pov_score.score()

            return MoveDecision(
                uci_move=result.move.uci() if result.move else "0000",
                score=score,
                time_taken_ms=time_taken_ms,
            )
        finally:
            await engine.quit()
