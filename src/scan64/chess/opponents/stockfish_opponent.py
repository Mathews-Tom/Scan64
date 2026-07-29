import time

import chess
import chess.engine

from scan64.chess.opponents.protocols import MoveDecision, OpponentContext, OpponentPolicy
from scan64.chess.positions.models import Position
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager


def _limit_for(context: OpponentContext) -> chess.engine.Limit:
    # Stockfish Skill Level works better with depth limits too, but time is fine.
    # We'll use a fast limit so tests don't take forever.
    if context.time_remaining_ms:
        return chess.engine.Limit(time=context.time_remaining_ms / 1000.0)
    return chess.engine.Limit(time=0.1)


def _decision_from(
    result: chess.engine.PlayResult, board: chess.Board, time_taken_ms: int
) -> MoveDecision:
    score = None
    if result.info and "score" in result.info:
        pov_score = result.info["score"].pov(board.turn)
        if pov_score.is_mate():
            # High arbitrary value for mate
            mate_v = pov_score.mate()
            score = 10000 if (mate_v is not None and mate_v > 0) else -10000
        else:
            score = pov_score.score()

    return MoveDecision(
        uci_move=result.move.uci() if result.move else "0000",
        score=score,
        time_taken_ms=time_taken_ms,
    )


class StockfishOpponentProvider(OpponentPolicy):
    def __init__(self, config: StockfishConfig, pool_manager: EnginePoolManager | None = None):
        self.config = config
        self.pool_manager = pool_manager

    async def choose_move(self, position: Position, context: OpponentContext) -> MoveDecision:
        """
        Choose a move for the given position using Stockfish.

        The context.strength_setting maps to Stockfish's Skill Level (0-20).
        This provides an approximate Elo range of 1320 - 3190.
        Note: The playstyle at reduced strength is NOT human-like. It relies on
        occasional blunders interspersed with strong moves, and is only meant
        for configurable conventional engine play prior to Maia integration (M21).

        When ``pool_manager`` is set, moves are served from the interactive
        engine pool so live play is never queued behind batch analysis and no
        new engine process is spawned per move. Without it (the pre-M41
        behaviour, retained for one release), a fresh process is spawned and
        torn down for this call only.
        """
        board = chess.Board(position.fen)
        skill_level = max(0, min(20, context.strength_setting))
        limit = _limit_for(context)

        if self.pool_manager is not None:
            start_time = time.monotonic()
            result = await self.pool_manager.play_interactive(position.fen, skill_level, limit)
            time_taken_ms = int((time.monotonic() - start_time) * 1000)
            return _decision_from(result, board, time_taken_ms)

        _, engine = await chess.engine.popen_uci(self.config.binary_path)
        try:
            await engine.configure(
                {
                    "Threads": self.config.threads,
                    "Hash": self.config.hash_size,
                    "Skill Level": skill_level,
                }
            )

            start_time = time.monotonic()
            result = await engine.play(board, limit, info=chess.engine.INFO_SCORE)
            time_taken_ms = int((time.monotonic() - start_time) * 1000)

            return _decision_from(result, board, time_taken_ms)
        finally:
            await engine.quit()
