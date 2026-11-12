"""Shared lesson and hint soundness policy (scores are side-to-move POV)."""

import math
from typing import Any, Literal

import chess

SOUND_MOVE_WIN_PROBABILITY_LOSS = 0.08


def win_probability(line: dict[str, Any]) -> float | None:
    """Convert centipawns using the Lichess logistic curve; mates are decisive.

    Older persisted principal variations may lack scores. They can prove the
    first move only, not the soundness of any alternative.
    """
    if line.get("score_mate") is not None:
        return 1.0 if int(line["score_mate"]) > 0 else 0.0
    if line.get("score_cp") is None:
        return None
    exponent = -0.00368208 * float(line["score_cp"])
    return 1.0 / (1.0 + math.exp(min(exponent, 700.0)))


ReviewBand = Literal["sound", "inaccuracy", "mistake", "blunder", "unscored"]
REVIEW_INACCURACY_LOSS = 0.15
REVIEW_MISTAKE_LOSS = 0.30


def review_loss(before: float | None, after: float | None) -> float | None:
    """Both probabilities use their position's side-to-move perspective."""
    if before is None or after is None:
        return None
    return min(1.0, max(0.0, before - (1.0 - after)))


def review_band(loss: float | None) -> ReviewBand:
    if loss is None:
        return "unscored"
    if loss <= SOUND_MOVE_WIN_PROBABILITY_LOSS:
        return "sound"
    if loss <= REVIEW_INACCURACY_LOSS:
        return "inaccuracy"
    if loss <= REVIEW_MISTAKE_LOSS:
        return "mistake"
    return "blunder"


def review_probability(board: chess.Board, line: dict[str, Any]) -> tuple[float | None, str | None]:
    """Exact automatic outcomes precede engine scores; never claim a draw.

    Call with the replay board, including its full move stack, so fivefold
    repetition is not confused with a FEN-only or claimable-draw position.
    """
    outcome = board.outcome(claim_draw=False)
    if outcome is not None:
        probability = 0.5 if outcome.winner is None else float(outcome.winner == board.turn)
        return probability, outcome.termination.name.lower()
    return win_probability(line), None
