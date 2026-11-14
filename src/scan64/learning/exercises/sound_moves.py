"""Select engine-proved alternatives and widen source-position searches."""

from typing import Any

import chess

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.tolerance import SOUND_MOVE_WIN_PROBABILITY_LOSS, win_probability
from scan64.providers.stockfish.adapter import EngineAdapter


def _line_move(board: chess.Board, line: dict[str, Any]) -> chess.Move:
    pv = line.get("pv")
    if not isinstance(pv, list) or not pv:
        raise ValueError("Engine analysis has no principal variation")
    if not isinstance(pv[0], str):
        raise ValueError("Engine analysis has an invalid principal variation")
    try:
        move = chess.Move.from_uci(pv[0])
    except ValueError:
        raise ValueError("Engine analysis has an invalid principal variation") from None
    if move not in board.legal_moves:
        raise ValueError("Engine analysis objective move is illegal in the lesson position")
    return move


def sound_moves(
    board: chess.Board, analysis: EngineAnalysis, *, avoid_mate: bool = False
) -> list[chess.Move]:
    """Keep the first line and every scored line within the shared tolerance.

    An unscored legacy first line still proves its move, but cannot establish
    a probability-loss bound for alternatives.
    """
    if not analysis.raw_result:
        raise ValueError("Engine analysis has no principal variation")
    best = analysis.raw_result[0]
    if avoid_mate and isinstance(best.get("score_mate"), int) and best["score_mate"] <= 0:
        raise ValueError("The lesson source is already a forced mate against the mover")
    moves = [_line_move(board, best)]
    best_probability = win_probability(best)
    if best_probability is None:
        return moves
    for line in analysis.raw_result[1:]:
        if avoid_mate and isinstance(line.get("score_mate"), int) and line["score_mate"] <= 0:
            continue
        probability = win_probability(line)
        if probability is None:
            continue
        if best_probability - probability <= SOUND_MOVE_WIN_PROBABILITY_LOSS:
            move = _line_move(board, line)
            if move not in moves:
                moves.append(move)
    return moves


async def analyze_lesson_source(
    adapter: EngineAdapter,
    fen: str,
    *,
    nodes: int = 1_000_000,
    initial_analysis: EngineAnalysis | None = None,
) -> EngineAnalysis:
    """Widen MultiPV until it brackets the sound set, or reaches eight lines."""
    initial_multipv = initial_analysis.config.get("multipv") if initial_analysis else None
    if initial_analysis is not None and (
        initial_analysis.config.get("nodes") != nodes or initial_multipv not in (2, 4, 8)
    ):
        raise ValueError(
            "Reusable source analysis must have the same node budget and MultiPV 2/4/8"
        )
    for multipv in (2, 4, 8):
        if isinstance(initial_multipv, int) and initial_multipv > multipv:
            continue
        analysis = (
            initial_analysis
            if initial_analysis is not None and initial_multipv == multipv
            else await adapter.analyze_position(fen, nodes=nodes, multipv=multipv)
        )
        # Validate the objective before persisting it as verification evidence.
        sound_moves(chess.Board(fen), analysis)
        best = win_probability(analysis.raw_result[0])
        last = win_probability(analysis.raw_result[-1])
        if (
            len(analysis.raw_result) < multipv
            or best is None
            or last is None
            or best - last > SOUND_MOVE_WIN_PROBABILITY_LOSS
        ):
            return analysis
    return analysis
