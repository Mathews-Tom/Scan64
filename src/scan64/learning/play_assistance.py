"""Grade proposed Play moves without changing the authoritative position."""

from __future__ import annotations

import math

import chess
from pydantic import BaseModel

from scan64.learning.tolerance import (
    ReviewBand,
    review_band,
    review_loss,
    review_probability,
)
from scan64.providers.stockfish.pool import EnginePoolManager


class PlayMoveGrade(BaseModel):
    move: str
    san: str
    band: ReviewBand
    win_probability_loss: float


async def _scored_probability(board: chess.Board, manager: EnginePoolManager) -> float:
    """Require a scored, legal first-ranked witness, never an unscored fallback."""
    terminal, _ = review_probability(board, {})
    if terminal is not None:
        return terminal
    analysis = await manager.analyze_hint(board)
    try:
        if not isinstance(analysis.raw_result, list) or not analysis.raw_result:
            raise ValueError("The engine returned no scored witness")
        line = analysis.raw_result[0]
        if not isinstance(line, dict):
            raise ValueError("The engine returned an invalid witness")
        cp, mate = line.get("score_cp"), line.get("score_mate")
        if not ((type(cp) is int and mate is None) or (cp is None and type(mate) is int)):
            raise ValueError("The engine returned no valid score")
        if mate == 0 or (cp is not None and not math.isfinite(float(cp))):
            raise ValueError("The engine returned an invalid active-position score")
        pv = line.get("pv")
        if not isinstance(pv, list) or not pv:
            raise ValueError("The engine returned no legal witness")
        continuation = board.copy(stack=False)
        for uci in pv:
            if not isinstance(uci, str):
                raise ValueError("The engine returned an invalid witness")
            move = chess.Move.from_uci(uci)
            if move not in continuation.legal_moves:
                raise ValueError("The engine returned an illegal witness")
            continuation.push(move)
        probability, _ = review_probability(board, line)
        if probability is None or not math.isfinite(probability):
            raise ValueError("The engine returned no usable score")
        return probability
    except (ValueError, OverflowError):
        manager.invalidate_hint(board)
        raise ValueError("The engine returned no scored legal witness") from None


async def grade_play_moves(
    board: chess.Board, moves: list[chess.Move], manager: EnginePoolManager
) -> list[PlayMoveGrade]:
    """Use one reserved source search and at most one search per proposed move.

    Branches retain the replay stack for exact automatic outcomes, including
    future repetitions inside engine searches. Calls use the existing hint
    admission, budgets and replay-keyed cache, sequentially so this
    request never occupies the interactive slot reserved for opponent moves.
    """
    if not board.is_valid() or board.is_game_over(claim_draw=False):
        raise ValueError("Play assistance requires an active legal position")
    if not 1 <= len(moves) <= 3 or len(set(moves)) != len(moves):
        raise ValueError("Supply one to three unique legal moves")
    if any(move not in board.legal_moves for move in moves):
        raise ValueError("Supply one to three unique legal moves")
    if manager.interactive_pool.concurrency < 2:
        raise TimeoutError("Play assistance requires at least two interactive engines")
    before = await _scored_probability(board, manager)
    grades: list[PlayMoveGrade] = []
    for move in moves:
        branch = board.copy()
        branch.push(move)
        after = await _scored_probability(branch, manager)
        loss = review_loss(before, after)
        if loss is None:
            raise ValueError("The engine returned no usable comparison")
        grades.append(
            PlayMoveGrade(
                move=move.uci(),
                san=board.san(move),
                band=review_band(loss),
                win_probability_loss=loss,
            )
        )
    return grades
