"""Factor module: the Stockfish Evaluation Guide's named terms, in Python.

``evaluate_factors(board)`` returns every guide term for White and Black, the
evaluation groups (Material, Pawns, King and the others) with middle-game,
end-game and tapered values, and the guide's final evaluation. It runs in
pure Python on python-chess, with no engine binary and no network access.

Source: Stockfish Evaluation Guide by hxim
(https://github.com/hxim/Stockfish-Evaluation-Guide), GPL-3.0, commit
63ff375bb5a8b7b2e4f965c3a3de7698fef7616c. The guide documents the classical
(hand-written) evaluation of the Stockfish 12 era.
"""

from .report import (
    GROUPS,
    PARTS,
    PAWN_VALUE_EG,
    Contribution,
    FactorReport,
    GroupValue,
    TermValue,
    evaluate_factors,
)
from .terms import TERMS

GUIDE_URL = "https://github.com/hxim/Stockfish-Evaluation-Guide"
GUIDE_COMMIT = "63ff375bb5a8b7b2e4f965c3a3de7698fef7616c"
TERM_NAMES: tuple[str, ...] = tuple(name for name, _, _ in TERMS)

__all__ = [
    "GROUPS",
    "GUIDE_COMMIT",
    "GUIDE_URL",
    "PARTS",
    "PAWN_VALUE_EG",
    "TERM_NAMES",
    "Contribution",
    "FactorReport",
    "GroupValue",
    "TermValue",
    "evaluate_factors",
]
