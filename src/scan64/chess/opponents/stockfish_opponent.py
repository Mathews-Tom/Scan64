import time

import chess
import chess.engine

from scan64.chess.opponents.protocols import MoveDecision, OpponentContext, OpponentPolicy
from scan64.chess.positions.models import Position
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, PooledStockfishAdapter


def _limit_for(context: OpponentContext) -> chess.engine.Limit:
    if context.nodes is not None:
        return chess.engine.Limit(nodes=context.nodes)
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

        New sessions use the session-seeded, node-limited bot ladder. Stored
        sessions without ``bot_level`` retain their legacy Skill Level path,
        including clamping old stored strengths to the engine's 0–20 range.

        When ``pool_manager`` is set, moves are served from the interactive
        engine pool so live play is never queued behind batch analysis and no
        new engine process is spawned per move. Without it (the pre-M41
        behaviour, retained for one release), a fresh process is spawned and
        torn down for this call only.
        """
        board = chess.Board(position.fen)
        if context.bot_level is not None:
            start_time = time.monotonic()
            if self.pool_manager is not None:
                result = await self.pool_manager.play_bot_interactive(position.fen, context)
            else:
                adapter = PooledStockfishAdapter(self.config)
                try:
                    await adapter.reset()
                    result = await adapter.play_bot_move(position.fen, context)
                finally:
                    await adapter.quit()
            return _decision_from(result, board, int((time.monotonic() - start_time) * 1000))
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
                    "UCI_LimitStrength": False,
                    "UCI_Elo": 1320,
                }
            )

            start_time = time.monotonic()
            result = await engine.play(board, limit, info=chess.engine.INFO_SCORE)
            time_taken_ms = int((time.monotonic() - start_time) * 1000)

            return _decision_from(result, board, time_taken_ms)
        finally:
            await engine.quit()
