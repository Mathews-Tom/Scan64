"""Shared lesson and hint soundness policy (scores are side-to-move POV)."""

import math
from typing import Any

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
