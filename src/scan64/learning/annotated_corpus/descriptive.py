"""English descriptive notation ("P-K4", "Kt × B P ch") read against a board.

A descriptive move names files from the mover's side and ranks from the
mover's side, and names pieces by the side of the board they started on
("K R" is the rook that started on h1 or h8). A move text is matched against
every legal move on the board; it converts only when exactly one legal move
fits. No guessing: an unmatched or ambiguous text raises ``DescriptiveError``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

import chess

PIECES = {
    "P": chess.PAWN,
    "Kt": chess.KNIGHT,
    "N": chess.KNIGHT,
    "B": chess.BISHOP,
    "R": chess.ROOK,
    "Q": chess.QUEEN,
    "K": chess.KING,
}
# The file a designator names, as python-chess file indices.
_SIDE_FILES = {"Q": {"R": 0, "Kt": 1, "N": 1, "B": 2}, "K": {"B": 5, "Kt": 6, "N": 6, "R": 7}}
_CENTRE_FILES = {"Q": 3, "K": 4}

_FILE = r"(?:[KQ](?:Kt|N|R|B)?|Kt|N|R|B)"
_PIECE = r"(?:Kt|N|K|Q|R|B|P)"
_SQUARE = rf"{_FILE}[1-8]"
_MOVE_RE = re.compile(
    rf"""^
    (?P<mfile>{_FILE})??(?P<mpiece>{_PIECE})
    (?:\((?P<from>{_SQUARE})\))?
    (?:
        -(?P<dest>{_SQUARE})
      | x(?P<tfile>{_FILE})??(?P<tpiece>{_PIECE})(?:\((?P<tsq>{_SQUARE})\))?
    )
    (?:=(?P<promo>Q|R|B|Kt|N)|\((?P<promo_paren>Q|R|B|Kt|N)\))?
    (?P<ep>e\.?p\.?)?
    (?P<check>ch|\+|dis\.?ch|dbl\.?ch|doublech|mate|\#)?
    $""",
    re.VERBOSE,
)
_CASTLE_RE = re.compile(
    r"^(?:Castles\(?(?P<side>KR|QR|K|Q)?\)?|(?P<oo>[O0]-[O0](?:-[O0])?))(?:ch|\+)?$"
)
_MARKS_RE = re.compile(r"(?:\((?:!|\?|!!|\?\?|!\?|\?!)\)|[!?]+)$")


class DescriptiveError(ValueError):
    """A descriptive move text matches no legal move, or more than one."""


@dataclass(frozen=True)
class _Spec:
    piece: chess.PieceType
    mover_files: frozenset[int] | None
    from_square: frozenset[chess.Square] | None
    dest: frozenset[chess.Square] | None
    target_piece: chess.PieceType | None
    target_files: frozenset[int] | None
    target_square: frozenset[chess.Square] | None
    promotion: chess.PieceType | None
    check: bool


def compact(text: str) -> str:
    """Spacing and symbols removed: ``"Kt × B P ch"`` becomes ``"KtxBPch"``."""
    text = text.replace("×", "x").replace("X", "x").replace("—", "-").replace("–", "-")
    text = re.sub(r"\s+|,", "", text).replace("KT", "Kt")
    text = _MARKS_RE.sub("", text.rstrip(".,;"))
    # "R-(B5)B7": the square the piece leaves, written after the hyphen.
    return re.sub(r"^([A-Za-z]+)-(\([A-Za-z]+[1-8]\))", r"\1\2-", text)


def _files(designator: str) -> frozenset[int]:
    if designator in _CENTRE_FILES:
        return frozenset({_CENTRE_FILES[designator]})
    if designator in _SIDE_FILES["K"]:
        # "Kt 3" or "B 4": either side's file of that piece.
        return frozenset(files[designator] for files in _SIDE_FILES.values())
    return frozenset({_SIDE_FILES[designator[0]][designator[1:]]})


def _qualifier(designator: str | None, piece: str | None) -> frozenset[int] | None:
    """The files a qualifier allows: "K" before a rook is the king's rook,
    before a pawn the king's file; "KB" before a pawn is the king's bishop's
    file."""
    if designator is None or piece is None:
        return None
    if designator in _CENTRE_FILES and piece != "P":
        return _files(designator + piece)
    return _files(designator)


def _squares(text: str, colour: chess.Color) -> frozenset[chess.Square]:
    rank = int(text[-1]) - 1
    if colour == chess.BLACK:
        rank = 7 - rank
    return frozenset(chess.square(file, rank) for file in _files(text[:-1]))


def _spec(text: str, colour: chess.Color) -> _Spec:
    match = _MOVE_RE.match(text)
    if match is None:
        raise DescriptiveError(f"not a descriptive move: {text!r}")
    groups = match.groupdict()
    piece = PIECES[groups["mpiece"]]
    return _Spec(
        piece=piece,
        mover_files=_qualifier(groups["mfile"], groups["mpiece"]),
        from_square=_squares(groups["from"], colour) if groups["from"] else None,
        dest=_squares(groups["dest"], colour) if groups["dest"] else None,
        target_piece=PIECES[groups["tpiece"]] if groups["tpiece"] else None,
        target_files=_qualifier(groups["tfile"], groups["tpiece"]),
        target_square=_squares(groups["tsq"], colour) if groups["tsq"] else None,
        promotion=PIECES[promo] if (promo := groups["promo"] or groups["promo_paren"]) else None,
        check=groups["check"] is not None,
    )


def _captured_square(board: chess.Board, move: chess.Move) -> chess.Square:
    if board.is_en_passant(move):
        return chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
    return move.to_square


def _identity_file(
    board: chess.Board, square: chess.Square, origins: Mapping[chess.Square, chess.Square | None]
) -> int | None:
    """A pawn is named by the file it stands on; a piece by the file it started on."""
    if board.piece_type_at(square) == chess.PAWN:
        return chess.square_file(square)
    origin = origins.get(square)
    return None if origin is None else chess.square_file(origin)


def _fits(
    board: chess.Board,
    move: chess.Move,
    spec: _Spec,
    origins: Mapping[chess.Square, chess.Square | None],
) -> bool:
    if board.piece_type_at(move.from_square) != spec.piece or board.is_castling(move):
        return False
    if spec.mover_files is not None:
        if _identity_file(board, move.from_square, origins) not in spec.mover_files:
            return False
    if spec.from_square is not None and move.from_square not in spec.from_square:
        return False
    if spec.dest is not None:
        if board.is_capture(move) or move.to_square not in spec.dest:
            return False
    else:
        if not board.is_capture(move):
            return False
        taken = _captured_square(board, move)
        if board.piece_type_at(taken) != spec.target_piece:
            return False
        if spec.target_files is not None:
            if _identity_file(board, taken, origins) not in spec.target_files:
                return False
        if spec.target_square is not None and taken not in spec.target_square:
            return False
    if move.promotion != spec.promotion:
        return False
    return not spec.check or board.gives_check(move)


def _castle(board: chess.Board, match: re.Match[str]) -> chess.Move:
    side, oo = match.group("side"), match.group("oo")
    long = (oo is not None and oo.count("-") == 2) or side in {"QR", "Q"}
    short = (oo is not None and oo.count("-") == 1) or side in {"KR", "K"}
    candidates = [
        move
        for move in board.legal_moves
        if board.is_castling(move)
        and (
            (board.is_kingside_castling(move) and not long)
            or (board.is_queenside_castling(move) and not short)
        )
    ]
    if len(candidates) != 1:
        raise DescriptiveError(
            f"castling {match.group(0)!r}: {len(candidates)} legal castling moves"
        )
    return candidates[0]


@dataclass(frozen=True)
class ParsedMove:
    move: chess.Move
    # True when the text fit a checking move too, and the missing "ch" decided.
    by_missing_check: bool


def parse_move(
    board: chess.Board, text: str, origins: Mapping[chess.Square, chess.Square | None]
) -> ParsedMove:
    """The one legal move ``text`` describes on ``board``.

    ``origins`` maps each occupied square to the square its piece started on
    (``None`` for a promoted piece), as ``Origins`` keeps it.
    """
    compacted = compact(text)
    castle = _CASTLE_RE.match(compacted)
    if castle is not None:
        return ParsedMove(_castle(board, castle), by_missing_check=False)
    spec = _spec(compacted, board.turn)
    candidates = [move for move in board.legal_moves if _fits(board, move, spec, origins)]
    if not candidates:
        raise DescriptiveError(f"{text!r}: no legal move fits")
    by_missing_check = False
    if len(candidates) > 1 and not spec.check:
        # The notation marks every check ("ch"); a move written without it
        # does not give check.
        quiet = [move for move in candidates if not board.gives_check(move)]
        if len(quiet) == 1:
            candidates, by_missing_check = quiet, True
    if len(candidates) > 1:
        sans = ", ".join(sorted(board.san(move) for move in candidates))
        raise DescriptiveError(f"{text!r}: ambiguous between {sans}")
    return ParsedMove(candidates[0], by_missing_check=by_missing_check)


class Origins:
    """Where each piece on the board started, kept move by move."""

    def __init__(self, board: chess.Board) -> None:
        if board.fen() != chess.STARTING_FEN:
            raise DescriptiveError("origins are tracked from the starting position only")
        self.squares: dict[chess.Square, chess.Square | None] = {
            square: square for square in chess.SQUARES if board.piece_at(square) is not None
        }

    def push(self, board: chess.Board, move: chess.Move) -> None:
        """Update for ``move``, before it is pushed on ``board``."""
        if board.is_castling(move):
            rank = chess.square_rank(move.from_square)
            kingside = board.is_kingside_castling(move)
            rook_from = chess.square(7 if kingside else 0, rank)
            rook_to = chess.square(5 if kingside else 3, rank)
            king_to = chess.square(6 if kingside else 2, rank)
            self.squares[king_to] = self.squares.pop(move.from_square)
            self.squares[rook_to] = self.squares.pop(rook_from)
            return
        self.squares.pop(_captured_square(board, move), None)
        origin = self.squares.pop(move.from_square)
        self.squares[move.to_square] = None if move.promotion else origin
