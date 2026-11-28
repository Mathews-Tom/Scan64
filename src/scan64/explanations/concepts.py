"""Chess concept library: code decides every chess term the narrator may name (#459).

Each concept has a detector over a python-chess board (plus the engine line
where the concept needs one) and a plain fact template that names the
concrete pieces and squares. Fact text never holds a score, a count or a
factor name. The model only words a fact; it never decides that a concept
holds.

Three groups, with the definitions Sydney approved on 2026-11-28:

- geometry (``move_concepts`` for one move, ``position_concepts`` for a
  position): skewer, fork, discovered attack or check, trapped piece,
  hanging piece, back-rank weakness, overloading;
- evaluation (``position_concepts``): passed, isolated, doubled and backward
  pawns, blockade, outpost, weak square, bad bishop, open and half-open
  file, space. Isolated, backward, outpost, weak square, rook files and
  space read the factor module (``explanations.factors``, Stockfish
  Evaluation Guide); passed, doubled, bad bishop and blockade take their
  textbook meaning;
- intent (``intent_concepts``), which need the engine line and scores:
  sacrifice, compensation, initiative, prophylaxis.

Check, interposition, pin, checkmate and stalemate belong to the
role-labelled fact generator (#460) and are not repeated here.

Piece values are 1, 3, 3, 5 and 9. A move "loses material" when its static
exchange loses at least one pawn.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

import chess

from scan64.explanations.factors.board import SQUARES, XS, YS, GuidePosition, positions_from_board
from scan64.explanations.factors.terms import (
    backward_sq,
    isolated_sq,
    outpost_sq,
    outpost_square_sq,
    rook_on_file_sq,
    space,
    space_area,
)
from scan64.learning.evidence.composer import PIECE_VALUES, line_material_gain

Concept = Literal[
    "skewer",
    "fork",
    "discovered_attack",
    "discovered_check",
    "trapped_piece",
    "hanging_piece",
    "back_rank_weakness",
    "overloading",
    "passed_pawn",
    "isolated_pawn",
    "doubled_pawns",
    "backward_pawn",
    "blockade",
    "outpost",
    "weak_square",
    "bad_bishop",
    "open_file",
    "half_open_file",
    "space",
    "sacrifice",
    "compensation",
    "initiative",
    "prophylaxis",
]

# Thresholds of the approved definitions, in pawns.
LOSS = 1  # a static exchange that loses at least this much "loses material"
SACRIFICE_MATERIAL = 2  # material the mover is down after the engine line
SACRIFICE_SCORE_CP = 50  # the move's score is this close to the best move's
INITIATIVE_RUN = 3  # forcing moves in a row by the mover
PROPHYLAXIS_GAIN = 1  # material the stopped reply would have won
CHECK_DEPTH = 2  # checks in a row a fork or skewer may answer and still hold
# Library choice (not in the guide): the side with more space controls at
# least this much more of the guide's space area (safe central squares,
# counted twice behind its own pawns).
SPACE_MARGIN = 3

_KING_VALUE = 100  # orders the king last among attackers; it is never captured
_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}
_COUNT_WORDS = ("no", "a", "two", "three", "four", "five", "six", "seven", "eight")
_SLIDERS = (chess.BISHOP, chess.ROOK, chess.QUEEN)
_DIRS: dict[chess.PieceType, tuple[tuple[int, int], ...]] = {
    chess.BISHOP: ((1, 1), (1, -1), (-1, 1), (-1, -1)),
    chess.ROOK: ((1, 0), (-1, 0), (0, 1), (0, -1)),
    chess.QUEEN: ((1, 1), (1, -1), (-1, 1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)),
}


@dataclass(frozen=True)
class ConceptFact:
    """One concept found in a position or a move, as a plain sentence.

    ``squares`` are the squares the text names; a fact about a whole file
    names none.
    """

    concept: Concept
    text: str
    squares: tuple[chess.Square, ...]


# ---------------------------------------------------------------- Helpers


def side_name(colour: chess.Color) -> str:
    return "White" if colour == chess.WHITE else "Black"


def piece_phrase(board: chess.Board, square: chess.Square) -> str:
    """``the white knight on e5``."""
    piece = board.piece_at(square)
    if piece is None:
        raise ValueError(f"no piece on {chess.square_name(square)}")
    colour = "white" if piece.color == chess.WHITE else "black"
    return f"the {colour} {_NAMES[piece.piece_type]} on {chess.square_name(square)}"


def _joined(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _value(piece: chess.Piece | None) -> int:
    if piece is None:
        return 0
    return _KING_VALUE if piece.piece_type == chess.KING else PIECE_VALUES[piece.piece_type]


def _captured_value(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return 1
    gain = _value(board.piece_at(move.to_square))
    if move.promotion is not None:
        gain += PIECE_VALUES[move.promotion] - 1
    return gain


def _recapture_gain(board: chess.Board, square: chess.Square) -> int:
    """Best gain for the side to move from recapturing on ``square`` (or not)."""
    captures = [m for m in board.legal_moves if m.to_square == square and board.is_capture(m)]
    if not captures:
        return 0
    move = min(captures, key=lambda m: (_value(board.piece_at(m.from_square)), -(m.promotion or 0)))
    gain = _captured_value(board, move)
    board.push(move)
    gain -= _recapture_gain(board, square)
    board.pop()
    return max(0, gain)


def static_exchange(board: chess.Board, move: chess.Move) -> int:
    """Material the side to move wins with ``move`` after the best recaptures.

    Each side recaptures on the target square with its least valuable piece,
    or stops when recapturing loses. Legal moves only, so pins count.
    """
    gain = _captured_value(board, move)
    board.push(move)
    gain -= _recapture_gain(board, move.to_square)
    board.pop()
    return gain


def _with_turn(board: chess.Board, colour: chess.Color) -> chess.Board | None:
    """A copy with ``colour`` to move, or None when the other side is in check."""
    copy = board.copy(stack=False)
    if copy.turn != colour:
        if copy.is_check():
            return None
        copy.push(chess.Move.null())
    return copy


def _san_line(board: chess.Board, moves: Iterable[chess.Move]) -> list[str]:
    replay = board.copy(stack=False)
    out = []
    for move in moves:
        out.append(replay.san(move))
        replay.push(move)
    return out


def _parse_line(board: chess.Board, line: Sequence[str]) -> list[chess.Move] | None:
    replay = board.copy(stack=False)
    moves = []
    for uci in line:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return None
        if move not in replay.legal_moves:
            return None
        moves.append(move)
        replay.push(move)
    return moves


def _first_rank(colour: chess.Color) -> int:
    return 0 if colour == chess.WHITE else 7


def _forward(colour: chess.Color) -> int:
    return 1 if colour == chess.WHITE else -1


# ---------------------------------------------------------------- Geometry


def hanging_pieces(board: chess.Board, colour: chess.Color) -> list[ConceptFact]:
    """Pieces of ``colour`` attacked and not defended, or attacked by a cheaper piece.

    As in the lesson diagnosis rule (``hanging_piece_proof``), pawns and the
    king are never called hanging.
    """
    facts = []
    for square, piece in board.piece_map().items():
        if piece.color != colour or piece.piece_type in (chess.PAWN, chess.KING):
            continue
        attackers = board.attackers(not colour, square)
        if not attackers:
            continue
        cheaper = [a for a in attackers if _value(board.piece_at(a)) < _value(piece)]
        if board.attackers(colour, square) and not cheaper:
            continue
        attacker = min(attackers, key=lambda a: _value(board.piece_at(a)))
        reason = "and nothing defends it" if not cheaper else "and is worth less"
        facts.append(
            ConceptFact(
                "hanging_piece",
                f"{piece_phrase(board, square)} is hanging: "
                f"{piece_phrase(board, attacker)} attacks it {reason}.",
                (square, attacker),
            )
        )
    return facts


def _loses_when_attacked(board: chess.Board, square: chess.Square) -> bool:
    """With the attacker to move, a capture on ``square`` wins at least LOSS."""
    return any(
        static_exchange(board, m) >= LOSS
        for m in board.legal_moves
        if m.to_square == square and board.is_capture(m)
    )


def trapped_pieces(board: chess.Board, colour: chess.Color) -> list[ConceptFact]:
    """Attacked pieces of ``colour`` that lose material staying and on every flight."""
    own = _with_turn(board, colour)
    enemy = _with_turn(board, not colour)
    if own is None or enemy is None:
        return []
    facts = []
    for square, piece in board.piece_map().items():
        if piece.color != colour or piece.piece_type in (chess.PAWN, chess.KING):
            continue
        if not board.attackers(not colour, square) or own.is_pinned(colour, square):
            continue
        if not _loses_when_attacked(enemy, square):
            continue
        if all(_flight_loses(own, m) for m in own.legal_moves if m.from_square == square):
            facts.append(
                ConceptFact(
                    "trapped_piece",
                    f"{piece_phrase(board, square)} is trapped: it is attacked, "
                    "and every square it can reach loses it.",
                    (square,),
                )
            )
    return facts


def _flight_loses(board: chess.Board, move: chess.Move) -> bool:
    gain = _captured_value(board, move)
    board.push(move)
    gain -= _recapture_gain(board, move.to_square)
    board.pop()
    return gain <= -LOSS


def back_rank_weakness(board: chess.Board, colour: chess.Color) -> list[ConceptFact]:
    """The ``colour`` king on its first rank with no square in front and a rank mate."""
    king = board.king(colour)
    rank = _first_rank(colour)
    if king is None or chess.square_rank(king) != rank:
        return []
    file = chess.square_file(king)
    front = [
        chess.square(f, rank + _forward(colour)) for f in (file - 1, file, file + 1) if 0 <= f < 8
    ]
    for square in front:
        piece = board.piece_at(square)
        if not (piece is not None and piece.color == colour) and not board.is_attacked_by(
            not colour, square
        ):
            return []
    enemy = _with_turn(board, not colour)
    if enemy is None:
        return []
    for move in enemy.legal_moves:
        mover = enemy.piece_at(move.from_square)
        if mover is None or mover.piece_type not in (chess.ROOK, chess.QUEEN):
            continue
        if chess.square_rank(move.to_square) != rank or not enemy.gives_check(move):
            continue
        san = enemy.san(move)
        enemy.push(move)
        mate = enemy.is_checkmate()
        enemy.pop()
        if mate:
            return [
                ConceptFact(
                    "back_rank_weakness",
                    f"{piece_phrase(board, king)} is weak on its back rank: it has no "
                    f"square to step forward to, so {piece_phrase(board, move.from_square)} "
                    f"can mate with {san}.",
                    (king, move.from_square, move.to_square),
                )
            ]
    return []


def overloaded_pieces(board: chess.Board, colour: chess.Color) -> list[ConceptFact]:
    """A ``colour`` piece that alone guards two attacked pieces and cannot keep both.

    The opponent takes one target, the defender recaptures, and the other
    target is then unguarded and taken. The fact needs that sequence to win
    material, so a recapture that wins a queen is no overload. A queen trade
    that then wins only a pawn is no overload either. The defender may be the
    king.
    """
    enemy = _with_turn(board, not colour)
    if enemy is None:
        return []
    guarded: dict[chess.Square, list[chess.Square]] = {}
    for square, piece in board.piece_map().items():
        if piece.color != colour or piece.piece_type == chess.KING:
            continue
        if not board.attackers(not colour, square):
            continue
        defenders = board.attackers(colour, square)
        if len(defenders) == 1:
            guarded.setdefault(next(iter(defenders)), []).append(square)
    facts = []
    for defender, targets in guarded.items():
        found = _overload(enemy, colour, defender, targets)
        if found is not None:
            first, second, line = found
            facts.append(
                ConceptFact(
                    "overloading",
                    f"{piece_phrase(board, defender)} is overloaded: it alone guards "
                    f"{piece_phrase(board, first)} and {piece_phrase(board, second)}, and "
                    f"after {' '.join(line)} {piece_phrase(board, second)} is left unguarded.",
                    (defender, first, second),
                )
            )
    return facts


def _overload(
    enemy: chess.Board,
    colour: chess.Color,
    defender: chess.Square,
    targets: Sequence[chess.Square],
) -> tuple[chess.Square, chess.Square, list[str]] | None:
    for first in targets:
        captures = [m for m in enemy.legal_moves if m.to_square == first]
        if not captures:
            continue
        take = min(captures, key=lambda m: _value(enemy.piece_at(m.from_square)))
        queen_trade = (
            enemy.piece_type_at(take.from_square) == chess.QUEEN
            and enemy.piece_type_at(first) == chess.QUEEN
        )
        recapture = chess.Move(defender, first)
        after = enemy.copy(stack=False)
        after.push(take)
        if recapture not in after.legal_moves:
            continue
        line = _san_line(enemy, (take, recapture))
        after.push(recapture)
        if _loses_when_attacked(after, first):
            continue  # the recapture loses the defender itself
        for second in targets:
            if second == first or after.attackers(colour, second):
                continue
            if queen_trade and after.piece_type_at(second) == chess.PAWN:
                continue  # a queen trade that wins a pawn
            for grab in after.legal_moves:
                if grab.to_square != second:
                    continue
                gain = line_material_gain(enemy, [take.uci(), recapture.uci(), grab.uci()])
                if gain is not None and gain >= LOSS:
                    return first, second, line
    return None


def _holds(
    after: chess.Board, wins: Callable[[chess.Board], bool], checks: int = CHECK_DEPTH
) -> bool:
    """``wins`` holds after every legal reply of the side to move in ``after``.

    A reply that gives check and breaks it only delays it when the mover has
    an answer to the check after which it holds again. Up to ``checks``
    checks in a row are followed.
    """
    for reply in list(after.legal_moves):
        after.push(reply)
        ok = wins(after) or (checks > 0 and after.is_check() and _answers(after, wins, checks - 1))
        after.pop()
        if not ok:
            return False
    return True


def _answers(board: chess.Board, wins: Callable[[chess.Board], bool], checks: int) -> bool:
    """The side in check has a reply after which ``wins`` holds again."""
    for evasion in list(board.legal_moves):
        board.push(evasion)
        ok = _holds(board, wins, checks)
        board.pop()
        if ok:
            return True
    return False


def _gains(board: chess.Board, move: chess.Move) -> bool:
    return move in board.legal_moves and static_exchange(board, move) >= LOSS


def fork(board: chess.Board, move: chess.Move) -> list[ConceptFact]:
    """After ``move`` the piece attacks two or more pieces, and one always falls."""
    after = board.copy(stack=False)
    after.push(move)
    forker = move.to_square
    mover = board.turn
    targets = [
        sq
        for sq in after.attacks(forker)
        if (p := after.piece_at(sq)) is not None and p.color != mover and p.piece_type != chess.PAWN
    ]
    if len(targets) < 2:
        return []

    def wins(b: chess.Board) -> bool:
        return any(
            _gains(b, chess.Move(forker, t)) or _gains(b, chess.Move(forker, t, chess.QUEEN))
            for t in targets
        )

    if not _holds(after, wins):
        return []
    names = [piece_phrase(after, t) for t in sorted(targets)]
    return [
        ConceptFact(
            "fork",
            f"{board.san(move)} is a fork: {piece_phrase(after, forker)} attacks "
            f"{_joined(names)}, and one of them is lost.",
            (forker, *sorted(targets)),
        )
    ]


def _ray_behind(
    board: chess.Board, square: chess.Square, piece_type: chess.PieceType
) -> list[tuple[chess.Square, chess.Square]]:
    """(front, behind) pairs: the first two pieces on each ray from ``square``."""
    pairs = []
    for df, dr in _DIRS[piece_type]:
        found: list[chess.Square] = []
        f, r = chess.square_file(square) + df, chess.square_rank(square) + dr
        while 0 <= f < 8 and 0 <= r < 8 and len(found) < 2:
            target = chess.square(f, r)
            if board.piece_at(target) is not None:
                found.append(target)
            f, r = f + df, r + dr
        if len(found) == 2:
            pairs.append((found[0], found[1]))
    return pairs


def _balance(board: chess.Board, colour: chess.Color) -> int:
    """``colour``'s material minus the opponent's."""
    return sum(
        PIECE_VALUES[p.piece_type] * (1 if p.color == colour else -1)
        for p in board.piece_map().values()
        if p.piece_type != chess.KING
    )


def _lost_to_reply(board: chess.Board, colour: chess.Color, start: int) -> int:
    """What ``colour`` has lost since ``start``, with the last reply's exchange played out."""
    reply = board.pop()
    lost = start - _balance(board, colour)
    if board.is_capture(reply) or reply.promotion is not None:
        lost += max(0, static_exchange(board, reply))
    board.push(reply)
    return lost


def _skewer_text(
    board: chess.Board, line_piece: chess.Square, front: chess.Square, behind: chess.Square
) -> str:
    """What the line piece takes; a king in front can only move."""
    name, target = piece_phrase(board, line_piece), piece_phrase(board, behind)
    line_name = _NAMES[board.piece_type_at(line_piece) or chess.QUEEN]
    if board.piece_type_at(front) == chess.KING:
        return f"when the king moves, {name} takes {target}."
    front_name = _NAMES[board.piece_type_at(front) or chess.QUEEN]
    return (
        f"if the {front_name} moves, {name} takes {target}; "
        f"if it stays, the {line_name} takes the {front_name}."
    )


def _skewers(
    after: chess.Board, attacker: chess.Square, fresh: chess.SquareSet | None = None
) -> list[tuple[chess.Square, chess.Square]]:
    """(front, behind) pairs the line piece on ``attacker`` skewers in ``after``.

    With ``fresh``, the front piece must stand on one of those squares.
    """
    piece = after.piece_at(attacker)
    if piece is None or piece.piece_type not in _SLIDERS:
        return []
    if not any(after.legal_moves):
        return []  # mate or stalemate: the front piece cannot move
    found = []
    start = _balance(after, piece.color)
    for front, behind in _ray_behind(after, attacker, piece.piece_type):
        if fresh is not None and front not in fresh:
            continue
        fp, bp = after.piece_at(front), after.piece_at(behind)
        if fp is None or bp is None or fp.color == piece.color or bp.color == piece.color:
            continue
        if bp.piece_type in (chess.KING, chess.PAWN):
            continue
        if fp.piece_type != chess.KING and _value(fp) <= _value(bp):
            continue

        def wins(
            b: chess.Board,
            front: chess.Square = front,
            behind: chess.Square = behind,
            fp: chess.Piece = fp,
            bp: chess.Piece = bp,
        ) -> bool:
            if b.piece_at(attacker) != piece:
                return False
            # The piece behind, or the front piece when that one stayed.
            gains = []
            if b.piece_at(behind) == bp and chess.Move(attacker, behind) in b.legal_moves:
                gains.append(static_exchange(b, chess.Move(attacker, behind)))
            take_front = chess.Move(attacker, front)
            if b.piece_at(front) == fp and _gains(b, take_front):
                gains.append(static_exchange(b, take_front))
            if not gains:
                return False
            lost = _lost_to_reply(b, piece.color, start)
            return lost <= 0 or max(gains) > lost

        if _holds(after, wins):
            found.append((front, behind))
    return found


def skewer(board: chess.Board, move: chess.Move) -> list[ConceptFact]:
    """After ``move`` a line piece hits a king or a dearer piece with a piece behind.

    The line piece is the piece that moved, or one the move uncovers (a
    discovered skewer). After every legal reply, or after the mover answers
    a check, it can take the piece behind, or the front piece when that one
    stays. The skewer does not count when the opponent's reply wins at least
    as much. As for forks, the piece behind is never a pawn.
    """
    mover = board.turn
    after = board.copy(stack=False)
    after.push(move)
    san = board.san(move)
    facts = []
    for front, behind in _skewers(after, move.to_square):
        facts.append(
            ConceptFact(
                "skewer",
                f"{san} skewers {piece_phrase(after, front)}: "
                f"{_skewer_text(after, move.to_square, front, behind)}",
                (move.to_square, front, behind),
            )
        )
    for square in sorted(_uncovered(board, after, move, mover)):
        fresh = after.attacks(square) & ~board.attacks(square)
        for front, behind in _skewers(after, square, fresh):
            facts.append(
                ConceptFact(
                    "skewer",
                    f"{san} opens a skewer: {piece_phrase(after, square)} attacks "
                    f"{piece_phrase(after, front)}, and "
                    f"{_skewer_text(after, square, front, behind)}",
                    (square, front, behind),
                )
            )
    return facts


def _uncovered(
    board: chess.Board, after: chess.Board, move: chess.Move, mover: chess.Color
) -> chess.SquareSet:
    """The mover's queens, rooks and bishops that did not move with ``move``."""
    moved = {move.to_square}
    if board.is_castling(move):
        rook_file = 5 if board.is_kingside_castling(move) else 3
        moved.add(chess.square(rook_file, chess.square_rank(move.to_square)))
    sliders = (
        after.pieces(chess.BISHOP, mover)
        | after.pieces(chess.ROOK, mover)
        | after.pieces(chess.QUEEN, mover)
    )
    return chess.SquareSet(sq for sq in sliders if sq not in moved)


def discovered_attacks(board: chess.Board, move: chess.Move) -> list[ConceptFact]:
    """A queen, rook or bishop that did not move attacks a new piece after ``move``."""
    mover = board.turn
    after = board.copy(stack=False)
    after.push(move)
    facts = []
    for square in _uncovered(board, after, move, mover):
        new = [
            t
            for t in after.attacks(square) & ~board.attacks(square)
            if (p := after.piece_at(t)) is not None
            and p.color != mover
            and p.piece_type != chess.PAWN
        ]
        for target in sorted(new):
            check = after.piece_type_at(target) == chess.KING
            kind: Concept = "discovered_check" if check else "discovered_attack"
            what = "check" if check else "attack"
            facts.append(
                ConceptFact(
                    kind,
                    f"{board.san(move)} is a discovered {what}: "
                    f"{piece_phrase(after, square)} now attacks {piece_phrase(after, target)}.",
                    (square, target),
                )
            )
    return facts


def move_concepts(board: chess.Board, move: chess.Move) -> list[ConceptFact]:
    """Geometry concepts the move ``move`` creates: fork, skewer, discovered attack."""
    if move not in board.legal_moves:
        raise ValueError(f"illegal move {move.uci()}")
    return [*fork(board, move), *skewer(board, move), *discovered_attacks(board, move)]


# ---------------------------------------------------------------- Evaluation


def _square(i: int, colour: chess.Color) -> chess.Square:
    """The python-chess square of padded guide index ``i`` in ``colour``'s view."""
    rank = 7 - YS[i] if colour == chess.WHITE else YS[i]
    return chess.square(XS[i], rank)


def _views(board: chess.Board) -> dict[chess.Color, GuidePosition] | None:
    try:
        white, black = positions_from_board(board)
    except ValueError:
        return None
    return {chess.WHITE: white, chess.BLACK: black}


def is_passed(board: chess.Board, square: chess.Square) -> bool:
    """Textbook: no enemy pawn ahead on the pawn's file or an adjacent one."""
    pawn = board.piece_at(square)
    if pawn is None or pawn.piece_type != chess.PAWN:
        return False
    file, rank, step = chess.square_file(square), chess.square_rank(square), _forward(pawn.color)
    for f in (file - 1, file, file + 1):
        if not 0 <= f < 8:
            continue
        r = rank + step
        while 0 <= r < 8:
            if board.piece_at(chess.square(f, r)) == chess.Piece(chess.PAWN, not pawn.color):
                return False
            r += step
    return True


def pawn_concepts(board: chess.Board, colour: chess.Color) -> list[ConceptFact]:
    """Passed, isolated, backward and doubled pawns of ``colour``."""
    views = _views(board)
    if views is None:
        return []
    view = views[colour]
    facts = []
    for i in SQUARES:
        if view.bd[i] != "P":
            continue
        square = _square(i, colour)
        name = piece_phrase(board, square)
        if is_passed(board, square):
            facts.append(ConceptFact("passed_pawn", f"{name} is a passed pawn.", (square,)))
        if isolated_sq(view, i):
            facts.append(
                ConceptFact(
                    "isolated_pawn",
                    f"{name} is isolated: no pawn of its own stands on a file next to it.",
                    (square,),
                )
            )
        if backward_sq(view, i):
            facts.append(
                ConceptFact(
                    "backward_pawn",
                    f"{name} is backward: no pawn of its own can support it, "
                    "and an enemy pawn controls the square in front of it.",
                    (square,),
                )
            )
    for file in range(8):
        pawns = sorted(s for s in board.pieces(chess.PAWN, colour) if chess.square_file(s) == file)
        if len(pawns) >= 2:
            names = [chess.square_name(s) for s in pawns]
            facts.append(
                ConceptFact(
                    "doubled_pawns",
                    f"{side_name(colour)} has doubled pawns on {_joined(names)}.",
                    tuple(pawns),
                )
            )
    return facts


def blockades(board: chess.Board, colour: chess.Color) -> list[ConceptFact]:
    """Pieces of ``colour`` on the square in front of an enemy passed pawn."""
    facts = []
    for pawn in sorted(board.pieces(chess.PAWN, not colour)):
        if not is_passed(board, pawn):
            continue
        front = pawn + 8 * _forward(not colour)
        blocker = board.piece_at(front)
        if blocker is not None and blocker.color == colour:
            facts.append(
                ConceptFact(
                    "blockade",
                    f"{piece_phrase(board, front)} blockades the passed pawn on "
                    f"{chess.square_name(pawn)}.",
                    (front, pawn),
                )
            )
    return facts


def _guards(board: chess.Board, square: chess.Square, colour: chess.Color) -> list[str]:
    return [
        chess.square_name(s)
        for s in sorted(board.attackers(colour, square))
        if board.piece_type_at(s) == chess.PAWN
    ]


def outposts_and_weak_squares(board: chess.Board, colour: chess.Color) -> list[ConceptFact]:
    """Outposts of ``colour`` and the empty weak squares of its opponent.

    The guide's outpost square: on the fourth to sixth rank from ``colour``'s
    side, guarded by its pawn, and no enemy pawn can ever attack it.
    """
    views = _views(board)
    if views is None:
        return []
    view = views[colour]
    facts = []
    for i in SQUARES:
        if not outpost_square_sq(view, i):
            continue
        square = _square(i, colour)
        pawns = _guards(board, square, colour)
        verb = "guards" if len(pawns) == 1 else "guard"
        guards = _joined([f"the {side_name(colour).lower()} pawn on {g}" for g in pawns])
        piece = board.piece_at(square)
        if outpost_sq(view, i):
            facts.append(
                ConceptFact(
                    "outpost",
                    f"{piece_phrase(board, square)} stands on an outpost: {guards} "
                    f"{verb} it, and no enemy pawn can drive it away.",
                    (square,),
                )
            )
        elif piece is None:
            facts.append(
                ConceptFact(
                    "weak_square",
                    f"{chess.square_name(square)} is a weak square for "
                    f"{side_name(not colour)}: no {side_name(not colour).lower()} pawn can "
                    f"attack it, and {guards} {verb} it.",
                    (square,),
                )
            )
    return facts


def bad_bishops(board: chess.Board, colour: chess.Color) -> list[ConceptFact]:
    """Textbook bad bishop: most own pawns on its colour, two or more of them fixed."""
    pawns = board.pieces(chess.PAWN, colour)
    facts = []
    for bishop in sorted(board.pieces(chess.BISHOP, colour)):
        shade = (chess.square_file(bishop) + chess.square_rank(bishop)) % 2
        same = [p for p in pawns if (chess.square_file(p) + chess.square_rank(p)) % 2 == shade]
        fixed = [
            p
            for p in same
            if board.piece_at(p + 8 * _forward(colour)) == chess.Piece(chess.PAWN, not colour)
        ]
        if len(fixed) >= 2 and 2 * len(same) > len(pawns):
            names = [chess.square_name(p) for p in sorted(fixed)]
            facts.append(
                ConceptFact(
                    "bad_bishop",
                    f"{piece_phrase(board, bishop)} is a bad bishop: its own pawns on "
                    f"{_joined(names)} are fixed on its colour and block it.",
                    (bishop, *sorted(fixed)),
                )
            )
    return facts


def file_concepts(board: chess.Board) -> list[ConceptFact]:
    """Open files, and rooks on open or half-open files (the guide's rook on file)."""
    facts = []
    for file in range(8):
        mask = chess.BB_FILES[file]
        if not board.pawns & mask:
            facts.append(
                ConceptFact(
                    "open_file",
                    f"the {chess.FILE_NAMES[file]}-file is open.",
                    (),
                )
            )
    views = _views(board)
    if views is None:
        return facts
    for colour, view in views.items():
        for i in SQUARES:
            kind = rook_on_file_sq(view, i)
            if not kind:
                continue
            square = _square(i, colour)
            file_name = chess.FILE_NAMES[chess.square_file(square)]
            if kind == 2:
                text = f"{piece_phrase(board, square)} stands on the open {file_name}-file."
                facts.append(ConceptFact("open_file", text, (square,)))
            else:
                text = (
                    f"{piece_phrase(board, square)} stands on the half-open {file_name}-file: "
                    f"only {side_name(not colour)} has a pawn on it."
                )
                facts.append(ConceptFact("half_open_file", text, (square,)))
    return facts


def space_advantage(board: chess.Board) -> list[ConceptFact]:
    """The side with the guide's space term and clearly more space-area squares."""
    views = _views(board)
    if views is None:
        return []
    area = {c: space_area(v) for c, v in views.items()}
    for colour in (chess.WHITE, chess.BLACK):
        if space(views[colour]) <= space(views[not colour]):
            continue
        if area[colour] < area[not colour] + SPACE_MARGIN:
            continue
        rank4 = 3 if colour == chess.WHITE else 4
        advanced = sorted(
            p
            for p in board.pieces(chess.PAWN, colour)
            if 2 <= chess.square_file(p) <= 5
            and (chess.square_rank(p) - rank4) * _forward(colour) >= 0
        )
        if not advanced:
            continue
        names = [chess.square_name(p) for p in advanced]
        return [
            ConceptFact(
                "space",
                f"{side_name(colour)} has more space: its pawns on {_joined(names)} "
                f"gain room behind them in the centre.",
                tuple(advanced),
            )
        ]
    return []


def position_concepts(board: chess.Board) -> list[ConceptFact]:
    """Every static geometry and evaluation concept in ``board``, for both sides."""
    facts: list[ConceptFact] = []
    for colour in (chess.WHITE, chess.BLACK):
        facts += hanging_pieces(board, colour)
        facts += trapped_pieces(board, colour)
        facts += back_rank_weakness(board, colour)
        facts += overloaded_pieces(board, colour)
        facts += pawn_concepts(board, colour)
        facts += blockades(board, colour)
        facts += outposts_and_weak_squares(board, colour)
        facts += bad_bishops(board, colour)
    facts += file_concepts(board)
    facts += space_advantage(board)
    return facts


# ---------------------------------------------------------------- Intent


def _material(board: chess.Board, colour: chess.Color) -> dict[chess.PieceType, int]:
    return {t: len(board.pieces(t, colour)) for t in _NAMES if t != chess.KING}


def _material_words(lost: dict[chess.PieceType, int]) -> str:
    parts = []
    for piece_type in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN):
        count = lost.get(piece_type, 0)
        if count <= 0:
            continue
        name = _NAMES[piece_type] + ("s" if count > 1 else "")
        parts.append(f"{_COUNT_WORDS[min(count, 8)]} {name}")
    return _joined(parts)


def _lost(
    start: dict[chess.PieceType, int], end: dict[chess.PieceType, int]
) -> dict[chess.PieceType, int]:
    return {t: start[t] - end[t] for t in start if start[t] > end[t]}


def sacrifice(
    board: chess.Board,
    move: chess.Move,
    line: Sequence[str],
    *,
    move_score_cp: int,
    best_score_cp: int,
) -> ConceptFact | None:
    """The mover ends the engine line at least two pawns down, close to the best score.

    ``line`` is the engine's best line after ``move``. Scores are the mover's
    view in centipawns; they decide the concept and never reach the text.
    """
    moves = _parse_line(board, [move.uci(), *line])
    if moves is None:
        return None
    gain = line_material_gain(board, [m.uci() for m in moves])
    if gain is None or gain > -SACRIFICE_MATERIAL:
        return None
    if best_score_cp - move_score_cp > SACRIFICE_SCORE_CP:
        return None
    mover = board.turn
    end = board.copy(stack=False)
    for m in moves:
        end.push(m)
    gave = _material_words(_lost(_material(board, mover), _material(end, mover)))
    got = _material_words(_lost(_material(board, not mover), _material(end, not mover)))
    for_what = f"for {got}" if got else "for no material"
    return ConceptFact(
        "sacrifice",
        f"{board.san(move)} is a sacrifice: after the engine's line {side_name(mover)} "
        f"has given up {gave} {for_what}, and the move is still close to the best.",
        (move.from_square, move.to_square),
    )


def compensation(board: chess.Board, move: chess.Move, line: Sequence[str]) -> list[ConceptFact]:
    """What the sacrificing side gains along the line. Call only after ``sacrifice``."""
    moves = _parse_line(board, [move.uci(), *line])
    if moves is None:
        return []
    mover = board.turn
    end = board.copy(stack=False)
    checks: list[str] = []
    for m in moves:
        if end.turn == mover and end.gives_check(m):
            checks.append(end.san(m))
        end.push(m)
    facts = []
    if checks:
        enemy_king = end.king(not mover)
        squares = (enemy_king,) if enemy_king is not None else ()
        facts.append(
            ConceptFact(
                "compensation",
                f"In return {side_name(mover)} attacks the {side_name(not mover).lower()} "
                f"king, giving check with {_joined(checks)}.",
                squares,
            )
        )
    start_king, end_king = board.king(not mover), end.king(not mover)
    rank = _first_rank(not mover)
    if (
        start_king is not None
        and end_king is not None
        and chess.square_rank(start_king) == rank
        and chess.square_rank(end_king) != rank
    ):
        facts.append(
            ConceptFact(
                "compensation",
                f"In return the {side_name(not mover).lower()} king is drawn out to "
                f"{chess.square_name(end_king)}.",
                (end_king,),
            )
        )
    for pawn in sorted(end.pieces(chess.PAWN, mover)):
        before = board.piece_at(pawn) == chess.Piece(chess.PAWN, mover) and is_passed(board, pawn)
        if is_passed(end, pawn) and not before:
            facts.append(
                ConceptFact(
                    "compensation",
                    f"In return {side_name(mover)} gets a passed pawn on "
                    f"{chess.square_name(pawn)}.",
                    (pawn,),
                )
            )
    for square in sorted(end.pieces(chess.ROOK, mover) | end.pieces(chess.QUEEN, mover)):
        file = chess.square_file(square)
        if not end.pawns & chess.BB_FILES[file] and board.pawns & chess.BB_FILES[file]:
            facts.append(
                ConceptFact(
                    "compensation",
                    f"In return {piece_phrase(end, square)} gets the newly opened "
                    f"{chess.FILE_NAMES[file]}-file.",
                    (square,),
                )
            )
    return facts


def _threat_targets(board: chess.Board) -> set[chess.Square]:
    """Squares where the side to move wins material with a capture now."""
    return {
        m.to_square
        for m in board.legal_moves
        if board.is_capture(m) and static_exchange(board, m) >= LOSS
    }


def _mates_in_one(board: chess.Board) -> bool:
    for m in board.legal_moves:
        board.push(m)
        mate = board.is_checkmate()
        board.pop()
        if mate:
            return True
    return False


def makes_threat(board: chess.Board, move: chess.Move) -> bool:
    """``move`` threatens a winning capture or mate that was not there before it."""
    mover = board.turn
    before = _threat_targets(board) - {move.from_square}
    after = board.copy(stack=False)
    after.push(move)
    if after.is_check() or after.is_game_over():
        return False
    after.push(chess.Move.null())
    if after.turn != mover:
        return False
    return bool(_threat_targets(after) - before) or _mates_in_one(after)


def is_forcing(board: chess.Board, move: chess.Move) -> bool:
    """A check, a capture, or a threat the reply must answer."""
    return board.gives_check(move) or board.is_capture(move) or makes_threat(board, move)


def initiative(board: chess.Board, move: chess.Move, line: Sequence[str]) -> ConceptFact | None:
    """``move`` and the mover's next moves in the engine line are all forcing."""
    moves = _parse_line(board, [move.uci(), *line])
    if moves is None:
        return None
    mover = board.turn
    replay = board.copy(stack=False)
    run: list[str] = []
    for m in moves:
        if replay.turn == mover:
            if not is_forcing(replay, m):
                break
            run.append(replay.san(m))
        replay.push(m)
    if len(run) < INITIATIVE_RUN:
        return None
    return ConceptFact(
        "initiative",
        f"{side_name(mover)} has the initiative: {_joined(run)} each force "
        f"{side_name(not mover)} to answer.",
        (move.to_square,),
    )


def prophylaxis(
    board: chess.Board,
    move: chess.Move,
    threat_line: Sequence[str],
    reply_line: Sequence[str],
) -> ConceptFact | None:
    """``move`` stops a reply that would have won material, and threatens nothing.

    ``threat_line`` is the opponent's best line if the mover passed before
    ``move`` (the engine on the null-move position). ``reply_line`` is the
    engine's best line after ``move``. The threat must win at least
    PROPHYLAXIS_GAIN; after ``move`` the opponent's best line wins less.
    """
    if board.is_check() or is_forcing(board, move):
        return None
    passed = board.copy(stack=False)
    passed.push(chess.Move.null())
    threat = _parse_line(passed, threat_line)
    if not threat:
        return None
    gain = line_material_gain(passed, [m.uci() for m in threat])
    if gain is None or gain < PROPHYLAXIS_GAIN:
        return None
    after = board.copy(stack=False)
    after.push(move)
    reply = _parse_line(after, reply_line)
    if not reply:
        return None
    reply_gain = line_material_gain(after, [m.uci() for m in reply])
    if reply_gain is None or reply_gain >= PROPHYLAXIS_GAIN:
        return None
    victim = _first_victim(passed, threat)
    if victim is None or _first_capture_square(passed, threat) == move.from_square:
        return None  # a piece stepping out of an attack is not prophylaxis
    return ConceptFact(
        "prophylaxis",
        f"{board.san(move)} is prophylaxis: it stops {side_name(not board.turn)}'s "
        f"{passed.san(threat[0])}, which would have won {victim}.",
        (move.to_square, threat[0].to_square),
    )


def _first_capture_square(board: chess.Board, moves: Sequence[chess.Move]) -> chess.Square | None:
    replay = board.copy(stack=False)
    for m in moves:
        if replay.turn == board.turn and replay.is_capture(m):
            return m.to_square
        replay.push(m)
    return None


def _first_victim(board: chess.Board, moves: Sequence[chess.Move]) -> str | None:
    """The first piece the threatening side takes along ``moves``."""
    replay = board.copy(stack=False)
    side = board.turn
    for m in moves:
        if replay.turn == side and replay.is_capture(m):
            square = m.to_square
            if replay.is_en_passant(m):
                square = m.to_square - 8 * _forward(side)
            return piece_phrase(replay, square)
        replay.push(m)
    return None


def intent_concepts(
    board: chess.Board,
    move: chess.Move,
    line: Sequence[str],
    *,
    move_score_cp: int,
    best_score_cp: int,
    threat_line: Sequence[str] | None = None,
) -> list[ConceptFact]:
    """Sacrifice (with what it gains), initiative and prophylaxis for ``move``.

    ``line`` is the engine's best line after ``move``. ``threat_line`` is the
    opponent's best line in the null-move position before ``move``; without
    it, prophylaxis is not decided.
    """
    if move not in board.legal_moves:
        raise ValueError(f"illegal move {move.uci()}")
    facts: list[ConceptFact] = []
    sac = sacrifice(board, move, line, move_score_cp=move_score_cp, best_score_cp=best_score_cp)
    if sac is not None:
        facts.append(sac)
        facts += compensation(board, move, line)
    if (fact := initiative(board, move, line)) is not None:
        facts.append(fact)
    if threat_line is not None and (fact := prophylaxis(board, move, threat_line, line)):
        facts.append(fact)
    return facts
