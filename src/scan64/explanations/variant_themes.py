"""Board measures for the variant root-cause themes (issue #402).

Each function reads one python-chess variant board with the variant's own
rules (drops, check counts, explosions) and returns plain board data. The
theme rules, thresholds and plain words live in ``themes.py``.

"The opponent could" measures look at the position as if the opponent were to
move: the board's turn is flipped and the en passant square cleared. The
window positions all have the learner to move, so this shows what the
opponent threatens if the learner does nothing against it.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess
import chess.variant

# Piece values in pawns, for ordering and for the pocket comparison.
VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
_BACK_RANKS = chess.BB_RANK_1 | chess.BB_RANK_8


def _flipped(board: chess.Board) -> chess.Board:
    """The position with the other side to move (no en passant)."""
    other = board.copy(stack=False)
    other.turn = not board.turn
    other.ep_square = None
    return other


def _ranked(board: chess.Board, squares: chess.SquareSet | list[int]) -> list[int]:
    """Squares ordered by the value of the piece on them, highest first."""

    def value(square: int) -> int:
        piece_type = board.piece_type_at(square)
        if piece_type == chess.KING:
            return 100
        return VALUES.get(piece_type, 0) if piece_type is not None else 0

    return sorted(squares, key=lambda s: (-value(s), s))


# --- Crazyhouse ---------------------------------------------------------------------


def pocket(board: chess.variant.CrazyhouseBoard, side: chess.Color) -> list[int]:
    """The piece types ``side`` holds in hand, one entry per piece, highest value first."""
    hand = board.pockets[side]
    return [
        piece_type
        for piece_type in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN)
        for _ in range(hand.count(piece_type))
    ]


def pocket_value(board: chess.variant.CrazyhouseBoard, side: chess.Color) -> int:
    """The value in pawns of the pieces ``side`` holds in hand."""
    return sum(VALUES[piece_type] for piece_type in pocket(board, side))


def king_drop_squares(board: chess.Board, side: chess.Color) -> list[int]:
    """Empty squares next to ``side``'s king that no other ``side`` piece guards.

    The other side can drop a piece there that only the king can take.
    """
    king = board.king(side)
    if king is None:
        return []
    found = []
    for square in chess.SquareSet(chess.BB_KING_ATTACKS[king]):
        if board.piece_at(square) is not None:
            continue
        guards = board.attackers(side, square) - chess.SquareSet.from_square(king)
        if not guards:
            found.append(square)
    return found


@dataclass(frozen=True)
class Drop:
    """A drop the opponent could play: the piece, its square and its main target."""

    piece_type: int
    square: int
    target: int  # the learner's king (check) or the piece the drop attacks
    check: bool


def _safe_for(board: chess.Board, square: int, dropper: chess.Color) -> bool:
    """No piece of the other side can take on ``square``; the king only when it is guarded."""
    defender = not dropper
    attackers = board.attackers(defender, square)
    king = board.king(defender)
    if king is not None and king in attackers:
        attackers = attackers - chess.SquareSet.from_square(king)
        if not board.attackers(dropper, square):
            return False  # the king takes the piece
    return not attackers


def drop_threats(board: chess.variant.CrazyhouseBoard, learner: chess.Color) -> list[Drop]:
    """The opponent's drops on safe squares that give check or attack a learner piece.

    A drop attacks a knight, bishop, rook or queen when that piece is worth
    more than the dropped one or no other learner piece guards it. Safe: no
    learner piece can take the dropped piece (the king only when no opponent
    piece guards the square).
    Ordered: checks first, then by the target's value, highest first.
    """
    opponent = not learner
    king = board.king(learner)
    found: list[Drop] = []
    for piece_type in sorted(set(pocket(board, opponent)), key=lambda p: -VALUES[p]):
        for square in chess.SquareSet(~board.occupied & chess.BB_ALL):
            if piece_type == chess.PAWN and chess.BB_SQUARES[square] & _BACK_RANKS:
                continue
            after = board.copy(stack=False)
            after.set_piece_at(square, chess.Piece(piece_type, opponent))
            if not _safe_for(after, square, opponent):
                continue
            attacked = after.attacks(square) & after.occupied_co[learner]
            if king is not None and king in attacked:
                found.append(Drop(piece_type, square, king, True))
                continue
            targets = [
                s
                for s in attacked & ~after.pawns
                if VALUES[after.piece_type_at(s) or chess.PAWN] > VALUES[piece_type]
                or not after.attackers(learner, s)
            ]
            if targets:
                found.append(Drop(piece_type, square, _ranked(after, targets)[0], False))

    def order(drop: Drop) -> tuple[int, int, int]:
        target = board.piece_type_at(drop.target)
        value = VALUES.get(target, 0) if target is not None else 0
        return (0 if drop.check else 1, -value, drop.square)

    return sorted(found, key=order)


# --- Three-check --------------------------------------------------------------------


def checks_given(board: chess.variant.ThreeCheckBoard, side: chess.Color) -> int:
    """How many checks ``side`` has given; three wins the game."""
    return 3 - board.remaining_checks[side]


def checking_moves(board: chess.Board, learner: chess.Color) -> list[chess.Move]:
    """The opponent's legal moves that would check the learner's king, if it were their move.

    A check already on the board counts only when its piece moves and checks
    again. Ordered by the moving piece's value, highest first, then by squares.
    """
    other = _flipped(board) if board.turn == learner else board.copy(stack=False)
    king = board.king(learner)
    if king is None:
        return []
    standing = board.attackers(not learner, king)  # a check already on the board is not new

    def checks(move: chess.Move) -> bool:
        after = other.copy(stack=False)
        after.push(move)
        return bool(after.attackers(not learner, king) - standing)

    moves = [move for move in other.legal_moves if checks(move)]

    def order(move: chess.Move) -> tuple[int, int, int]:
        piece_type = other.piece_type_at(move.from_square) or chess.PAWN
        return (-VALUES[piece_type], move.from_square, move.to_square)

    return sorted(moves, key=order)


# --- Atomic -------------------------------------------------------------------------


def explosion_threats(board: chess.variant.AtomicBoard, victim: chess.Color) -> list[chess.Move]:
    """The other side's legal captures whose explosion would destroy ``victim``'s king.

    Read with the other side to move. Ordered by the capturing piece's value,
    lowest first (the cheapest piece to give up), then by squares.
    """
    attacker = not victim
    other = _flipped(board) if board.turn != attacker else board.copy(stack=False)
    found = []
    for move in other.legal_moves:
        if not other.is_capture(move):
            continue
        after = other.copy(stack=False)
        after.push(move)
        if after.king(victim) is None:
            found.append(move)

    def order(move: chess.Move) -> tuple[int, int, int]:
        piece_type = other.piece_type_at(move.from_square) or chess.PAWN
        return (VALUES[piece_type], move.from_square, move.to_square)

    return sorted(found, key=order)


__all__ = [
    "VALUES",
    "Drop",
    "checking_moves",
    "checks_given",
    "drop_threats",
    "explosion_threats",
    "king_drop_squares",
    "pocket",
    "pocket_value",
]
