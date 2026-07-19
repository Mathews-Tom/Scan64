from __future__ import annotations

import chess
import chess.engine

from scan64.chess.opponents.protocols import MoveDecision, OpponentContext
from scan64.chess.positions.models import Position
from scan64.providers.maia.config import MaiaConfig


class MaiaRuntimeError(RuntimeError):
    """Raised when a configured Maia engine cannot produce a legal move."""


class MaiaOpponentProvider:
    def __init__(self, config: MaiaConfig) -> None:
        self.config = config

    async def choose_move(
        self,
        position: Position,
        context: OpponentContext,
    ) -> MoveDecision:
        selection = self.config.select(context.strength_setting)
        self.config.validate_runtime(selection)
        board = chess.Board(position.fen)
        _, engine = await chess.engine.popen_uci(
            [str(self.config.binary_path), f"--weights={selection.checkpoint.weights_path}"]
        )
        try:
            await engine.configure({"Threads": self.config.threads})
            result = await engine.play(board, chess.engine.Limit(time=0.1))
        finally:
            await engine.quit()

        if result.move is None:
            raise MaiaRuntimeError("Configured Maia engine returned no move")
        if result.move not in board.legal_moves:
            raise MaiaRuntimeError(
                f"Configured Maia engine returned an illegal move: {result.move.uci()}"
            )

        return MoveDecision(
            uci_move=result.move.uci(),
            score=None,
            time_taken_ms=None,
        )
