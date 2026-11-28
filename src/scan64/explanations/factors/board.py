"""Board model of the Stockfish Evaluation Guide, with attack tables.

The guide addresses a square as ``(x, y)``: ``x`` is the file (0 = a) and
``y`` counts down from the eighth rank (0 = rank 8). Every term reads the
board from White's side; Black's values come from ``colorflip``, the board
mirrored top to bottom with the piece colours swapped.

This module stores the board in a padded 16 x 16 array, so offsets of up to
four squares never leave the array. Cells off the board hold ``"x"`` and
empty squares hold ``"-"``, as in the guide. The attack tables use the guide's
rules: bishops x-ray through queens, rooks through rooks and queens, and a
pinned piece attacks only along its pin line. Some guide terms read attacks on
squares just off the board (for example a passed pawn on the a-file asks about
the file left of it); the tables keep those cells too, so the port reproduces
the guide's values there.
"""

from __future__ import annotations

import chess

PAD = 4
STRIDE = 16
SIZE = STRIDE * STRIDE
EMPTY = "-"
OFF = "x"


def idx(x: int, y: int) -> int:
    """Padded index of guide square (x, y)."""
    return (y + PAD) * STRIDE + x + PAD


XS: tuple[int, ...] = tuple(i % STRIDE - PAD for i in range(SIZE))
YS: tuple[int, ...] = tuple(i // STRIDE - PAD for i in range(SIZE))
FLIP: tuple[int, ...] = tuple(idx(XS[i], 7 - YS[i]) for i in range(SIZE))
ON_BOARD: tuple[bool, ...] = tuple(0 <= XS[i] <= 7 and 0 <= YS[i] <= 7 for i in range(SIZE))
# The guide's ``sum`` visits files in the outer loop and ranks in the inner loop.
SQUARES: tuple[int, ...] = tuple(idx(x, y) for x in range(8) for y in range(8))

KING_DIRS: tuple[tuple[int, int], ...] = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)
DIAG_DIRS: tuple[tuple[int, int], ...] = tuple((dx, dy) for dx, dy in KING_DIRS if dx and dy)
ORTH_DIRS: tuple[tuple[int, int], ...] = tuple((dx, dy) for dx, dy in KING_DIRS if not (dx and dy))
KING_OFFSETS: tuple[int, ...] = tuple(dx + STRIDE * dy for dx, dy in KING_DIRS)
KNIGHT_OFFSETS: tuple[int, ...] = tuple(
    dx + STRIDE * dy
    for dx, dy in ((-1, 2), (-1, -2), (1, 2), (1, -2), (-2, 1), (-2, -1), (2, 1), (2, -1))
)

_SLIDER_DIRS = {"B": DIAG_DIRS, "R": ORTH_DIRS, "Q": KING_DIRS}
# Squares a slider may look through, per the guide's xray rules.
_PASSABLE = {"B": "-Qq", "R": "-RQq", "Q": "-"}


class GuidePosition:
    """One side's view of a position plus the cached tables the terms read.

    ``flip`` is the colour-flipped view; the two views reference each other.
    """

    __slots__ = (
        "bd",
        "castling",
        "white_to_move",
        "rule50",
        "flip",
        "memo",
        "king",
        "enemy_king",
        "pin",
        "pawn_att",
        "king_att",
        "knight_att",
        "bishop_att",
        "rook_att",
        "queen_att",
        "queen_diag_att",
        "att",
        "targets",
        "pawn_files",
    )

    def __init__(
        self,
        bd: list[str],
        castling: tuple[bool, bool, bool, bool],
        white_to_move: bool,
        rule50: int,
    ) -> None:
        self.bd = bd
        self.castling = castling
        self.white_to_move = white_to_move
        self.rule50 = rule50
        self.flip: GuidePosition = self
        self.memo: dict[object, object] = {}
        self.king = _find(bd, "K")
        self.enemy_king = _find(bd, "k")
        self.pin = _pins(bd, self.king)
        self._attacks()
        files = [0] * 10
        for i in SQUARES:
            if bd[i] == "P":
                files[XS[i] + 1] += 1
        self.pawn_files = files

    def _attacks(self) -> None:
        bd = self.bd
        pin = self.pin
        pawn = [0] * SIZE
        king = [0] * SIZE
        knight = [0] * SIZE
        bishop = [0] * SIZE
        rook = [0] * SIZE
        queen = [0] * SIZE
        qdiag = [0] * SIZE
        targets: dict[int, list[int]] = {}
        for i in SQUARES:
            c = bd[i]
            if c == "P":
                pawn[i - STRIDE - 1] += 1
                pawn[i - STRIDE + 1] += 1
            elif c == "K":
                for o in KING_OFFSETS:
                    king[i + o] = 1
            elif c == "N":
                ts = [] if pin[i] > 0 else [i + o for o in KNIGHT_OFFSETS]
                for t in ts:
                    knight[t] += 1
                targets[i] = ts
            elif c in _SLIDER_DIRS:
                table = bishop if c == "B" else rook if c == "R" else queen
                passable = _PASSABLE[c]
                pd = pin[i]
                ts = []
                for dx, dy in _SLIDER_DIRS[c]:
                    if pd != 0 and abs(dx + 3 * dy) != pd:
                        continue
                    step = dx + STRIDE * dy
                    diag = c == "Q" and dx != 0 and dy != 0
                    t = i
                    for _ in range(7):
                        t += step
                        table[t] += 1
                        if diag:
                            qdiag[t] += 1
                        ts.append(t)
                        if bd[t] not in passable:
                            break
                targets[i] = ts
        self.pawn_att = pawn
        self.king_att = king
        self.knight_att = knight
        self.bishop_att = bishop
        self.rook_att = rook
        self.queen_att = queen
        self.queen_diag_att = qdiag
        self.att = [
            pawn[i] + king[i] + knight[i] + bishop[i] + rook[i] + queen[i] for i in range(SIZE)
        ]
        self.targets = targets


def _find(bd: list[str], piece: str) -> int:
    for i in SQUARES:
        if bd[i] == piece:
            return i
    raise ValueError(f"the guide needs exactly one {piece!r} on the board")


def _pins(bd: list[str], king: int) -> list[int]:
    """The guide's ``Pinned direction`` for every square.

    A piece of either colour between the white king and a black slider gets
    ``abs(ix + 3 * iy)`` of the pin line, negative for a black piece.
    """
    pin = [0] * SIZE
    for dx, dy in KING_DIRS:
        step = dx + STRIDE * dy
        t = king + step
        while bd[t] == EMPTY:
            t += step
        piece = bd[t]
        if piece == OFF:
            continue
        u = t + step
        while bd[u] == EMPTY:
            u += step
        slider = bd[u]
        diagonal = dx != 0 and dy != 0
        if slider == "q" or (slider == "b" and diagonal) or (slider == "r" and not diagonal):
            value = abs(dx + 3 * dy)
            pin[t] = value if piece.isupper() else -value
    return pin


def positions_from_board(board: chess.Board) -> tuple[GuidePosition, GuidePosition]:
    """Return the White view and the colour-flipped view of ``board``."""
    if (
        len(board.pieces(chess.KING, chess.WHITE)) != 1
        or len(board.pieces(chess.KING, chess.BLACK)) != 1
    ):
        raise ValueError("the factor module needs exactly one king per side")
    if board.pawns & (chess.BB_RANK_1 | chess.BB_RANK_8):
        raise ValueError("the factor module does not accept pawns on the first or last rank")
    bd = [OFF] * SIZE
    fl = [OFF] * SIZE
    for i in SQUARES:
        bd[i] = EMPTY
        fl[i] = EMPTY
    for square, piece in board.piece_map().items():
        x = chess.square_file(square)
        y = 7 - chess.square_rank(square)
        symbol = piece.symbol()
        bd[idx(x, y)] = symbol
        fl[idx(x, 7 - y)] = symbol.swapcase()
    castling = (
        board.has_kingside_castling_rights(chess.WHITE),
        board.has_queenside_castling_rights(chess.WHITE),
        board.has_kingside_castling_rights(chess.BLACK),
        board.has_queenside_castling_rights(chess.BLACK),
    )
    white = GuidePosition(bd, castling, board.turn == chess.WHITE, board.halfmove_clock)
    black = GuidePosition(
        fl,
        (castling[2], castling[3], castling[0], castling[1]),
        board.turn != chess.WHITE,
        board.halfmove_clock,
    )
    white.flip = black
    black.flip = white
    return white, black
