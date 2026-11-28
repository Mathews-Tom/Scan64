"""Role-labelled facts the narrator words one at a time (#460, part of #457).

Code turns a position into short, plain facts; a small model rewrites one fact
at a time and never decides chess. Each fact carries:

- its role in the story: the move, what it misses, what follows, and what the
  mover could have played instead;
- one sentence that states one relation per clause, with explicit timing
  ("gives check; after the king moves, the knight takes the rook on a8") and
  the square every piece stands on after it moves;
- its relations as data (takes, attacks, defends, gives check, mates,
  stalemates, blocks, pins, shuts in, frees, opens, prevents, leaves
  undefended, moves), for the check in the voice pipeline.

python-chess decides every fact from the position, the played move, the
engine's best line from the same position and the engine's line after the
played move. No search runs here. Intent terms (blockade, outpost,
prophylaxis) wait for the concept library (#459); no fact names a factor, a
number of pawns or a centipawn value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import chess

Role = Literal["the_move", "what_it_misses", "what_follows", "could_have_played"]
ROLES: tuple[Role, ...] = ("the_move", "what_it_misses", "what_follows", "could_have_played")

RelationKind = Literal[
    "moves",
    "takes",
    "attacks",
    "gives_check",
    "mates",
    "stalemates",
    "blocks",
    "pins",
    "frees",
    "prevents",
    "defends",
    "undefended",
    "shuts_in",
    "opens",
]
Direction = Literal["forward", "back", "sideways"]

# Pieces a moved piece's attacks are stated for: the king is stated by the
# check, pawns would bury the story in noise.
_ATTACK_TARGETS = (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}
# Plies of the line after the played move that "What follows" states: the
# opponent's reply, the mover's answer, and the opponent's follow-up.
FOLLOW_PLIES = 3


class RoleFactsError(ValueError):
    """The moves or lines are not legal from the position."""


@dataclass(frozen=True)
class PieceOnSquare:
    colour: chess.Color
    piece: chess.PieceType
    square: chess.Square

    def phrase(self) -> str:
        return f"the {chess.piece_name(self.piece)} on {chess.square_name(self.square)}"

    def owned_phrase(self) -> str:
        return (
            f"{colour_name(self.colour)}'s {chess.piece_name(self.piece)} "
            f"on {chess.square_name(self.square)}"
        )


@dataclass(frozen=True)
class Relation:
    """One relation a fact states, by python-chess on ``fen`` and ``move``.

    ``subject`` is the piece that acts (after the move, on its new square);
    ``target`` the piece or square it acts on; ``through`` the third piece of a
    pin (the king it is pinned to) or of a block (the checking piece).
    """

    kind: RelationKind
    move: str
    # The position ``move`` is played from.
    fen: str
    subject: PieceOnSquare | None = None
    target: PieceOnSquare | None = None
    through: PieceOnSquare | None = None
    direction: Direction | None = None
    # The square an ``opens`` relation makes reachable.
    square: chess.Square | None = None


@dataclass(frozen=True)
class RoleFact:
    role: Role
    text: str
    relations: tuple[Relation, ...]


def colour_name(colour: chess.Color) -> str:
    return "White" if colour == chess.WHITE else "Black"


def role_label(role: Role, mover: chess.Color) -> str:
    """The role as the voice pipeline names it to the model."""
    labels: dict[Role, str] = {
        "the_move": "The move",
        "what_it_misses": "What it misses",
        "what_follows": "What follows",
        "could_have_played": f"What {colour_name(mover)} could have played",
    }
    return labels[role]


def numbered_san(board: chess.Board, move: chess.Move) -> str:
    """``5.Nxf7`` for White, ``7...Nf3#`` for Black."""
    dots = "." if board.turn == chess.WHITE else "..."
    return f"{board.fullmove_number}{dots}{board.san(move)}"


def direction(colour: chess.Color, move: chess.Move) -> Direction:
    """Forward, back or sideways from the mover's side of the board."""
    ranks = chess.square_rank(move.to_square) - chess.square_rank(move.from_square)
    if colour == chess.BLACK:
        ranks = -ranks
    if ranks > 0:
        return "forward"
    if ranks < 0:
        return "back"
    return "sideways"


def _parse(board: chess.Board, uci: str) -> chess.Move:
    try:
        move = chess.Move.from_uci(uci)
    except ValueError as error:
        raise RoleFactsError(f"Not a UCI move: {uci!r}") from error
    if move not in board.legal_moves:
        raise RoleFactsError(f"{uci} is not legal in {board.fen()}")
    return move


def _replay(board: chess.Board, line: Sequence[str]) -> list[chess.Move]:
    scratch = board.copy(stack=False)
    moves = []
    for uci in line:
        move = _parse(scratch, uci)
        moves.append(move)
        scratch.push(move)
    return moves


def _on(board: chess.Board, square: chess.Square) -> PieceOnSquare:
    piece = board.piece_at(square)
    if piece is None:
        raise RoleFactsError(f"No piece on {chess.square_name(square)}")
    return PieceOnSquare(piece.color, piece.piece_type, square)


def _mobility(board: chess.Board, square: chess.Square) -> int:
    """Pseudo-legal moves of the piece on ``square``, whichever side is to move."""
    piece = board.piece_at(square)
    if piece is None:
        return 0
    scratch = board.copy(stack=False)
    scratch.turn = piece.color
    scratch.ep_square = None
    return sum(1 for move in scratch.pseudo_legal_moves if move.from_square == square)


def _shut_in(board: chess.Board, colour: chess.Color) -> set[chess.Square]:
    """Squares of ``colour``'s pieces (not pawns or the king) that cannot move."""
    return {
        square
        for square in chess.SQUARES
        if (piece := board.piece_at(square)) is not None
        and piece.color == colour
        and piece.piece_type not in (chess.PAWN, chess.KING)
        and _mobility(board, square) == 0
    }


def _pinner(board: chess.Board, square: chess.Square) -> PieceOnSquare | None:
    """The enemy slider that pins the piece on ``square`` to its king."""
    piece = board.piece_at(square)
    if piece is None or not board.is_pinned(piece.color, square):
        return None
    for ray_square in board.pin(piece.color, square):
        other = board.piece_at(ray_square)
        if other is not None and other.color != piece.color and other.piece_type != chess.KING:
            return PieceOnSquare(other.color, other.piece_type, ray_square)
    return None


def _moved(board: chess.Board, move: chess.Move) -> PieceOnSquare:
    """The piece ``move`` moves, on the square it stands on after the move."""
    after = board.copy(stack=False)
    after.push(move)
    return _on(after, move.to_square)


def _king(board: chess.Board, colour: chess.Color) -> PieceOnSquare:
    square = board.king(colour)
    if square is None:
        raise RoleFactsError(f"{colour_name(colour)} has no king")
    return PieceOnSquare(colour, chess.KING, square)


@dataclass(frozen=True)
class _Said:
    text: str
    relation: Relation
    # Further relations the same sentence states, one per clause.
    more: tuple[Relation, ...] = ()


def _move_sentences(board: chess.Board, move: chess.Move, *, owned: bool) -> list[_Said]:
    """Facts about ``move`` from ``board``: the move itself, then what it does."""
    mover = board.turn
    label = numbered_san(board, move)
    uci = move.uci()
    fen = board.fen()
    before = _on(board, move.from_square)
    who = before.owned_phrase() if owned else before.phrase()
    after = board.copy(stack=False)
    after.push(move)
    moved = _on(after, move.to_square)
    said: list[_Said] = []

    if board.is_castling(move):
        side = "kingside" if chess.square_file(move.to_square) > 4 else "queenside"
        said.append(
            _Said(
                f"{label}: {colour_name(mover)} castles {side}; the king now stands on "
                f"{chess.square_name(move.to_square)}.",
                Relation("moves", uci, fen, subject=moved, direction="sideways"),
            )
        )
    elif board.is_capture(move):
        captured_square = (
            move.to_square
            if not board.is_en_passant(move)
            else chess.square(
                chess.square_file(move.to_square), chess.square_rank(move.from_square)
            )
        )
        captured = _on(board, captured_square)
        said.append(
            _Said(
                f"{label}: {who} takes {captured.phrase()}.",
                Relation("takes", uci, fen, subject=moved, target=captured),
            )
        )
    else:
        way = direction(mover, move)
        verb = "steps" if before.piece == chess.KING else "goes"
        said.append(
            _Said(
                f"{label}: {who} {verb} {way} from {chess.square_name(move.from_square)} "
                f"to {chess.square_name(move.to_square)}.",
                Relation("moves", uci, fen, subject=moved, direction=way),
            )
        )
    if move.promotion is not None:
        said.append(
            _Said(
                f"The pawn on {chess.square_name(move.to_square)} becomes a "
                f"{chess.piece_name(move.promotion)}.",
                Relation("moves", uci, fen, subject=moved),
            )
        )

    if board.is_check():
        checkers = board.checkers()
        if before.piece != chess.KING and move.to_square not in checkers:
            checker = _on(board, next(iter(checkers)))
            said.append(
                _Said(
                    f"{moved.phrase().capitalize()} blocks the check from {checker.phrase()}.",
                    Relation("blocks", uci, fen, subject=moved, through=checker),
                )
            )
            others = [
                numbered_san(board, other)
                for other in board.legal_moves
                if other != move
                and board.piece_type_at(other.from_square) != chess.KING
                and other.to_square not in checkers
            ]
            # A judgement on the played move only; for an alternative it reads as noise.
            if others and not owned:
                said.append(
                    _Said(
                        f"{label} was not the only way to block the check: "
                        f"{_joined(others)} also {'block' if len(others) > 1 else 'blocks'} it.",
                        Relation("blocks", uci, fen, subject=moved, through=checker),
                    )
                )

    opponent = not mover
    if after.is_checkmate():
        said.append(
            _Said(
                f"{label} gives check to {_king(after, opponent).phrase()}, and it is mate: "
                f"the game ends, and {colour_name(mover)} wins.",
                Relation("mates", uci, fen, subject=moved, target=_king(after, opponent)),
            )
        )
        return said
    if after.is_stalemate():
        said.append(
            _Said(
                f"{label} is stalemate: {colour_name(opponent)} has no legal move and is not "
                "in check, so the game ends at once as a draw.",
                Relation("stalemates", uci, fen, subject=moved),
            )
        )
        return said
    if after.is_check():
        for checker_square in after.checkers():
            checker = _on(after, checker_square)
            said.append(
                _Said(
                    f"{checker.phrase().capitalize()} gives check to "
                    f"{_king(after, opponent).phrase()}.",
                    Relation(
                        "gives_check", uci, fen, subject=checker, target=_king(after, opponent)
                    ),
                )
            )

    for square in after.attacks(move.to_square):
        target = after.piece_at(square)
        if target is None or target.color == mover or target.piece_type not in _ATTACK_TARGETS:
            continue
        attacked = PieceOnSquare(target.color, target.piece_type, square)
        said.append(
            _Said(
                f"{moved.phrase().capitalize()} attacks {attacked.phrase()}.",
                Relation("attacks", uci, fen, subject=moved, target=attacked),
            )
        )

    for square in after.attacks(move.to_square):
        own = after.piece_at(square)
        if own is None or own.color != mover or own.piece_type not in _ATTACK_TARGETS:
            continue
        if board.attackers(mover, square):
            continue
        defended = PieceOnSquare(own.color, own.piece_type, square)
        said.append(
            _Said(
                f"{moved.phrase().capitalize()} defends {defended.phrase()}.",
                Relation("defends", uci, fen, subject=moved, target=defended),
            )
        )

    back_rank = 0 if mover == chess.WHITE else 7
    king_before = _king_squares(board, mover)
    if (
        before.piece != chess.KING
        and king_before
        and all(chess.square_rank(square) == back_rank for square in king_before)
    ):
        king = _king(board, mover)
        opened = _king_squares(after, mover) - king_before
        for square in sorted(sq for sq in opened if chess.square_rank(sq) != back_rank):
            said.append(
                _Said(
                    f"Now {king.phrase()} can step to {chess.square_name(square)}.",
                    Relation("opens", uci, fen, subject=king, square=square),
                )
            )

    for square in chess.SQUARES:
        target = after.piece_at(square)
        if target is None or target.color != opponent or target.piece_type == chess.KING:
            continue
        if _pinner(after, square) == moved and _pinner(board, square) is None:
            pinned = PieceOnSquare(target.color, target.piece_type, square)
            said.append(
                _Said(
                    f"{moved.phrase().capitalize()} pins {pinned.phrase()} to "
                    f"{_king(after, opponent).phrase()}.",
                    Relation(
                        "pins",
                        uci,
                        fen,
                        subject=moved,
                        target=pinned,
                        through=_king(after, opponent),
                    ),
                )
            )
    return said


def _freed(board: chess.Board, move: chess.Move) -> set[chess.Square]:
    """Squares of the mover's pieces that could not move before ``move`` and can after."""
    after = board.copy(stack=False)
    after.push(move)
    return _shut_in(board, board.turn) - _shut_in(after, board.turn)


def _free_sentences(board: chess.Board, move: chess.Move) -> list[_Said]:
    label = numbered_san(board, move)
    return [
        _Said(
            f"{label} frees {_on(board, square).phrase()}: it could not move before.",
            Relation("frees", move.uci(), board.fen(), target=_on(board, square)),
        )
        for square in sorted(_freed(board, move))
    ]


def _line_sentences(board: chess.Board, moves: Sequence[chess.Move]) -> list[_Said]:
    """Facts for the first plies of a line, with a capture that follows a check timed."""
    said: list[_Said] = []
    scratch = board.copy(stack=False)
    for index, move in enumerate(moves[:FOLLOW_PLIES]):
        if index == 0:
            said.extend(_move_sentences(scratch, move, owned=True))
        elif index == 2 and scratch.move_stack and moves[0].to_square == move.from_square:
            # The checking piece takes after the reply: state the timing explicitly.
            if scratch.is_capture(move) and _gave_check(board, moves[0]):
                captured = _on(scratch, move.to_square)
                reply_piece = scratch.piece_type_at(moves[1].to_square)
                answer = (
                    "after the king moves"
                    if reply_piece == chess.KING
                    else f"after {colour_name(not board.turn)} answers the check"
                )
                moved_before = _on(scratch, move.from_square)
                after = scratch.copy(stack=False)
                after.push(move)
                said.append(
                    _Said(
                        f"{numbered_san(board, moves[0])} gives check; {answer}, "
                        f"{moved_before.phrase()} takes {captured.phrase()} "
                        f"({numbered_san(scratch, move)}).",
                        Relation(
                            "takes",
                            move.uci(),
                            scratch.fen(),
                            subject=_on(after, move.to_square),
                            target=captured,
                        ),
                    )
                )
        scratch.push(move)
        if scratch.is_game_over():
            break
    return said


def _gave_check(board: chess.Board, move: chess.Move) -> bool:
    return board.gives_check(move)


def _king_squares(board: chess.Board, colour: chess.Color) -> set[chess.Square]:
    """Squares ``colour``'s king can step to, whichever side is to move."""
    scratch = board.copy(stack=False)
    scratch.turn = colour
    king = scratch.king(colour)
    return {move.to_square for move in scratch.legal_moves if move.from_square == king}


def _blockers(board: chess.Board, square: chess.Square) -> list[PieceOnSquare]:
    """The piece's own pieces on the squares it attacks: what shuts it in."""
    colour = board.color_at(square)
    return [
        _on(board, target)
        for target in sorted(board.attacks(square))
        if board.color_at(target) == colour
    ]


def _phrases(pieces: Sequence[PieceOnSquare], *, own: str) -> str:
    """'its own pawns on f7, g7 and h7', or each piece named when the kinds differ."""
    squares = [chess.square_name(piece.square) for piece in pieces]
    if len({piece.piece for piece in pieces}) == 1:
        name = chess.piece_name(pieces[0].piece)
        plural = name if len(pieces) == 1 else f"{name}s"
        return f"{own}{plural} on {_joined(squares)}"
    return _joined([piece.phrase() for piece in pieces])


def _threat_sentences(
    board: chess.Board, played: chess.Move, follow: Sequence[chess.Move]
) -> list[_Said]:
    """The opponent's attacks the played move leaves.

    A piece attacked by a cheaper piece or not defended, and a pawn the line
    after the move takes.
    """
    mover = board.turn
    after = board.copy(stack=False)
    after.push(played)
    taken = follow[0].to_square if follow and after.is_capture(follow[0]) else None
    label = numbered_san(board, played)
    said: list[_Said] = []
    for square in chess.SQUARES:
        piece = after.piece_at(square)
        if piece is None or piece.color != mover or piece.piece_type == chess.KING:
            continue
        attackers = sorted(after.attackers(not mover, square))
        if not attackers or (piece.piece_type == chess.PAWN and square != taken):
            continue
        defended = bool(after.attackers(mover, square))
        values = {a: _VALUES[after.piece_type_at(a) or chess.PAWN] for a in attackers}
        cheapest = min(attackers, key=lambda a: values[a])
        if defended and values[cheapest] >= _VALUES[piece.piece_type] and square != taken:
            continue
        attacker_square = follow[0].from_square if square == taken else cheapest
        attacker = _on(after, attacker_square)
        target = PieceOnSquare(piece.color, piece.piece_type, square)
        already = "already " if attacker_square in board.attackers(not mover, square) else ""
        said.append(
            _Said(
                f"{attacker.owned_phrase()} {already}attacks {target.owned_phrase()}.",
                Relation("attacks", played.uci(), board.fen(), subject=attacker, target=target),
            )
        )
        if not defended:
            said.append(
                _Said(
                    f"After {label} nothing defends {target.phrase()}.",
                    Relation("undefended", played.uci(), board.fen(), target=target),
                )
            )
    return said


def _new_pins(board: chess.Board, move: chess.Move) -> list[tuple[PieceOnSquare, PieceOnSquare]]:
    """(pinned, pinner) for the mover's pieces ``move`` leaves pinned that were not before."""
    mover = board.turn
    after = board.copy(stack=False)
    after.push(move)
    pins = []
    for square in chess.SQUARES:
        piece = after.piece_at(square)
        if piece is None or piece.color != mover or piece.piece_type == chess.KING:
            continue
        pinner = _pinner(after, square)
        was = square != move.to_square and _pinner(board, square) is not None
        if pinner is not None and not was:
            pins.append((PieceOnSquare(piece.color, piece.piece_type, square), pinner))
    return pins


def _mobility_legal(board: chess.Board, square: chess.Square) -> int:
    """Legal moves of the piece on ``square``, with its side to move."""
    colour = board.color_at(square)
    if colour is None:
        return 0
    scratch = board.copy(stack=False)
    scratch.turn = colour
    return sum(1 for move in scratch.legal_moves if move.from_square == square)


def _joined(items: Sequence[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def role_facts(
    fen: str,
    played: str,
    best: str,
    *,
    best_line: Sequence[str] = (),
    played_line: Sequence[str] = (),
) -> tuple[RoleFact, ...]:
    """The role-labelled facts of ``played`` in ``fen`` against the engine's ``best``.

    ``best_line`` is the engine's line from ``fen`` and starts with ``best``
    (empty means ``best`` alone). ``played_line`` is the engine's line from the
    position after ``played``, without ``played``.
    """
    try:
        board = chess.Board(fen)
    except ValueError as error:
        raise RoleFactsError(f"Invalid FEN {fen!r}: {error}") from error
    played_move = _parse(board, played)
    best_moves = _replay(board, best_line or (best,))
    if best_moves[0].uci() != best:
        raise RoleFactsError(f"best_line starts with {best_moves[0].uci()}, not {best}")
    after_played = board.copy(stack=False)
    after_played.push(played_move)
    follow_moves = _replay(after_played, played_line)
    fen = board.fen()

    facts: list[RoleFact] = []

    def add(role: Role, said: Sequence[_Said]) -> None:
        facts.extend(RoleFact(role, item.text, (item.relation, *item.more)) for item in said)

    played_label = numbered_san(board, played_move)
    add("the_move", _move_sentences(board, played_move, owned=False))
    add("the_move", _free_sentences(board, played_move))

    best_move = best_moves[0]
    best_label = numbered_san(board, best_move)
    after_best = board.copy(stack=False)
    after_best.push(best_move)
    opponent = not board.turn
    still_shut = _freed(board, best_move) - _freed(board, played_move)
    misses: list[_Said] = []
    if played != best:
        misses.extend(_threat_sentences(board, played_move, follow_moves))
        for pinned, pinner in _new_pins(board, played_move):
            king = _king(after_played, pinned.colour)
            misses.append(
                _Said(
                    f"{pinner.owned_phrase()} pins {pinned.phrase()} to {king.phrase()}.",
                    Relation("pins", played, fen, subject=pinner, target=pinned, through=king),
                )
            )
        # A pin the better move uses: the piece it attacks cannot get away.
        for square in after_best.attacks(best_move.to_square):
            holder = _pinner(after_best, square)
            if (
                holder is None
                or holder.square == best_move.to_square
                or after_best.color_at(square) != opponent
            ):
                continue
            held = _on(after_best, square)
            king = _king(after_best, opponent)
            consequence = "cannot move" if _mobility_legal(after_best, square) == 0 else "is pinned"
            misses.append(
                _Said(
                    f"{held.owned_phrase()} {consequence}, because "
                    f"{holder.phrase()} pins it to {king.phrase()}.",
                    Relation("pins", best, fen, subject=holder, target=held, through=king),
                )
            )
        # A mate the better move gives: the king's own pieces shut it in.
        if after_best.is_checkmate():
            king = _king(board, opponent)
            blockers = [
                piece for piece in _blockers(after_best, king.square) if piece.colour == opponent
            ]
            if blockers and all(piece.piece == chess.PAWN for piece in blockers):
                misses.append(
                    _Said(
                        f"{king.owned_phrase()} is shut in by "
                        f"{_phrases(blockers, own='its own ')}.",
                        Relation("shuts_in", best, fen, subject=king, target=blockers[0]),
                        tuple(
                            Relation("shuts_in", best, fen, subject=king, target=piece)
                            for piece in blockers[1:]
                        ),
                    )
                )
        for square in sorted(still_shut):
            shut = _on(board, square)
            by = _blockers(board, square)
            misses.append(
                _Said(
                    f"{shut.phrase().capitalize()} was already shut in by "
                    f"{_phrases(by, own='the ')} before {played_label}, and {played_label} "
                    "does not free it.",
                    Relation("shuts_in", played, fen, subject=shut, target=by[0]),
                    tuple(
                        Relation("shuts_in", played, fen, subject=shut, target=piece)
                        for piece in by[1:]
                    ),
                )
            )
    add("what_it_misses", misses)

    follows = _line_sentences(after_played, follow_moves)
    if follow_moves:
        reply = follow_moves[0]
        after_reply = after_played.copy(stack=False)
        after_reply.push(reply)
        for pinned, pinner in _new_pins(board, played_move):
            if reply.to_square in after_reply.attacks(pinned.square):
                king = _king(after_reply, pinned.colour)
                follows.append(
                    _Said(
                        f"{pinned.phrase().capitalize()} cannot take "
                        f"{_on(after_reply, reply.to_square).phrase()}: {pinner.phrase()} pins "
                        f"it to {king.phrase()}.",
                        Relation(
                            "pins",
                            reply.uci(),
                            after_played.fen(),
                            subject=pinner,
                            target=pinned,
                            through=king,
                        ),
                    )
                )
    add("what_follows", follows)

    if played != best:
        alternative: list[_Said] = [
            _Said(
                f"{colour_name(board.turn)} could have played {best_label} "
                f"instead of {played_label}.",
                Relation("moves", best, fen, subject=_moved(board, best_move)),
            )
        ]
        alternative.extend(_line_sentences(board, best_moves))
        alternative.extend(
            _Said(
                f"{best_label} frees {_on(board, square).phrase()}.",
                Relation("frees", best, fen, target=_on(board, square)),
            )
            for square in sorted(still_shut)
        )
        if follow_moves:
            threat = follow_moves[0]
            if after_best.is_check() and threat not in after_best.legal_moves:
                piece = _on(after_played, threat.from_square)
                alternative.append(
                    _Said(
                        f"After {best_label}, {colour_name(after_best.turn)} must answer "
                        f"the check, so {piece.phrase()} cannot play "
                        f"{numbered_san(after_played, threat)} at once.",
                        Relation("prevents", best, fen, target=piece),
                    )
                )
        add("could_have_played", alternative)
    return tuple(facts)
