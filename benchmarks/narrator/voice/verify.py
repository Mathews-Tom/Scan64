# ruff: noqa: E501
# The clause texts are keys copied verbatim from the facts in data.py, one per line.
"""Every chess fact the voice runs fed a model, checked with python-chess, clause by clause.

``clauses`` splits a fact into its statements (sentences, and parts joined by ", and", ", so",
", which", "because" or ":"). ``CLAUSE_CLAIMS`` maps every clause of every fact of every run
version (runs 1-3, 4, 5-8 and the held-out run 9) to the claims it makes, and ``CLAIMS`` checks
each claim on the board. A test fails when a clause has no claim or a claim does not hold.

"Better was X" and "White could have played X instead of Y" are checked as board facts: X is a
legal move in the same position as the played move Y. That X is better is the engine's judgement
(#457), not a python-chess fact. The example facts in the prompts (1.Qd2, 1.Rb1, 1.a3) belong to
no position and are not here.
"""

import re
from collections.abc import Callable

import chess

from benchmarks.narrator.voice import data

sq = chess.parse_square
SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])|,\s+(?:and|so|which)\s+|\s+because\s+|:\s+")


def clauses(fact: str) -> list[str]:
    """The statements of a fact, without their end marks."""
    return [c.strip().rstrip(".,") for c in SPLIT.split(fact) if c.strip()]


def board(test: str, *sans: str) -> chess.Board:
    fen = data.FENS_HELDOUT[test] if test in data.FENS_HELDOUT else data.FENS[test]
    b = chess.Board(fen)
    for san in sans:
        b.push_san(san)
    return b


def legal(test: str, *sans: str) -> bool:
    try:
        board(test, *sans)
    except ValueError:
        return False
    return True


def legal_instead(test: str, alternative: str, played: str) -> bool:
    """Both moves are legal in the same position, and they differ."""
    start = board(test)
    try:
        return start.parse_san(alternative) != start.parse_san(played)
    except ValueError:
        return False


def piece(b: chess.Board, square: str, kind: chess.PieceType, color: chess.Color) -> bool:
    return b.piece_at(sq(square)) == chess.Piece(kind, color)


def attacks(b: chess.Board, frm: str, to: str) -> bool:
    return sq(to) in b.attacks(sq(frm))


def captures(test: str, san: str, square: str, kind: chess.PieceType) -> bool:
    before = board(test)
    move = before.parse_san(san)
    taken = before.piece_at(sq(square))
    return move.to_square == sq(square) and taken is not None and taken.piece_type == kind


def white_to_move(b: chess.Board) -> chess.Board:
    b = b.copy(stack=False)
    b.turn = chess.WHITE
    return b


def moves_from(b: chess.Board, square: str) -> list[chess.Move]:
    return [m for m in b.legal_moves if m.from_square == sq(square)]


def king_squares(b: chess.Board) -> set[int]:
    return {m.to_square for m in b.legal_moves if b.piece_type_at(m.from_square) == chess.KING}


def every_reply_then(test: str, sans: tuple[str, ...], king_only: bool, then: str) -> bool:
    """After ``sans``, every legal reply is a king move (if asked) and leaves ``then`` legal."""
    b = board(test, *sans)
    replies = list(b.legal_moves)
    if not replies:
        return False
    for reply in replies:
        if king_only and b.piece_type_at(reply.from_square) != chess.KING:
            return False
        b.push(reply)
        try:
            b.parse_san(then)
        except ValueError:
            return False
        finally:
            b.pop()
    return True


W, B = chess.WHITE, chess.BLACK
P1, P2, P3, P4, P5 = (
    "P1-greedy-knight",
    "P2-boxed-king",
    "P3-back-rank",
    "P4-fork",
    "P5-shut-bishop",
)
H1, H2, H3, H4, H5 = "H1-missed-mate", "H2-skewer", "H3-stalemate", "H4-pin", "H5-allowed-fork"

CLAIMS: dict[str, Callable[[], bool]] = {
    # P1: 5.Nxf7 grabs a pawn; 5...Qxg2 follows; 5.Bxf7+ takes the same pawn with check.
    "p1.nxf7-legal": lambda: legal(P1, "Nxf7"),
    "p1.nxf7-takes-pawn-f7": lambda: captures(P1, "Nxf7", "f7", chess.PAWN),
    "p1.knight-f7-attacks-rook-h8": lambda: (
        attacks(board(P1, "Nxf7"), "f7", "h8") and piece(board(P1), "h8", chess.ROOK, B)
    ),
    "p1.queen-g5-attacks-pawn-g2": lambda: (
        piece(board(P1), "g5", chess.QUEEN, B)
        and piece(board(P1), "g2", chess.PAWN, W)
        and attacks(board(P1), "g5", "g2")
    ),
    "p1.g2-undefended-after-nxf7": lambda: not board(P1, "Nxf7").attackers(W, sq("g2")),
    "p1.qxg2-follows": lambda: legal(P1, "Nxf7", "Qxg2"),
    "p1.queen-g2-attacks-rook-h1": lambda: (
        attacks(board(P1, "Nxf7", "Qxg2"), "g2", "h1") and piece(board(P1), "h1", chess.ROOK, W)
    ),
    "p1.bxf7-legal-instead-of-nxf7": lambda: legal_instead(P1, "Bxf7+", "Nxf7"),
    "p1.bxf7-takes-pawn-with-check": lambda: (
        captures(P1, "Bxf7+", "f7", chess.PAWN) and board(P1, "Bxf7+").is_check()
    ),
    "p1.black-in-check-after-bxf7": lambda: (
        board(P1, "Bxf7+").is_check() and bool(list(board(P1, "Bxf7+").legal_moves))
    ),
    "p1.after-bxf7-only-king-moves": lambda: all(
        board(P1, "Bxf7+").piece_type_at(m.from_square) == chess.KING
        for m in board(P1, "Bxf7+").legal_moves
    ),
    # P2: 7.Be2 blocks the check but is pinned; 7...Nf3 mates; 7.Qe2 blocks and attacks.
    "p2.check-from-queen-e4": lambda: (
        board(P2).checkers() == chess.SquareSet([sq("e4")])
        and piece(board(P2), "e4", chess.QUEEN, B)
    ),
    "p2.be2-blocks-the-check": lambda: legal(P2, "Be2") and not board(P2, "Be2").was_into_check(),
    "p2.nf3-follows": lambda: legal(P2, "Be2", "Nf3"),
    "p2.nf3-is-checkmate": lambda: board(P2, "Be2", "Nf3").is_checkmate(),
    "p2.queen-e4-pins-bishop-e2": lambda: (
        board(P2, "Be2", "Nf3").is_pinned(W, sq("e2"))
        and sq("e4") in board(P2, "Be2", "Nf3").pin(W, sq("e2"))
    ),
    "p2.bishop-e2-cannot-take-f3": lambda: (
        chess.Move(sq("e2"), sq("f3")) not in board(P2, "Be2", "Nf3").legal_moves
    ),
    "p2.qe2-legal-instead-of-be2": lambda: legal_instead(P2, "Qe2", "Be2"),
    "p2.qe2-blocks-the-check": lambda: legal(P2, "Qe2") and not board(P2, "Qe2").was_into_check(),
    "p2.queen-e2-attacks-queen-e4": lambda: attacks(board(P2, "Qe2"), "e2", "e4"),
    "p2.be2-not-the-only-block": lambda: (
        sum(
            1 for m in board(P2).legal_moves if board(P2).piece_type_at(m.from_square) != chess.KING
        )
        >= 2
    ),
    # P3: 1.h3 makes a free square; Black's king is shut in; 1.Rd8 mates.
    "p3.h3-legal": lambda: legal(P3, "h3"),
    "p3.king-g1-can-step-to-h2": lambda: (
        chess.Move(sq("g1"), sq("h2")) in white_to_move(board(P3, "h3")).legal_moves
    ),
    "p3.h3-frees-only-h2": lambda: (
        king_squares(white_to_move(board(P3, "h3"))) - king_squares(board(P3)) == {sq("h2")}
    ),
    "p3.black-king-g8-shut-in-by-f7-g7-h7": lambda: (
        piece(board(P3), "g8", chess.KING, B)
        and all(piece(board(P3), s, chess.PAWN, B) for s in ("f7", "g7", "h7"))
    ),
    "p3.rd8-legal-instead-of-h3": lambda: legal_instead(P3, "Rd8", "h3"),
    "p3.rd8-is-checkmate": lambda: board(P3, "Rd8").is_checkmate(),
    # P4: 1.Kd2 misses the fork 1.Nc7+.
    "p4.kd2-legal": lambda: legal(P4, "Kd2"),
    "p4.nc7-legal-instead-of-kd2": lambda: legal_instead(P4, "Nc7+", "Kd2"),
    "p4.nc7-check-and-attacks-rook-a8": lambda: (
        board(P4, "Nc7+").is_check()
        and attacks(board(P4, "Nc7+"), "c7", "a8")
        and piece(board(P4), "a8", chess.ROOK, B)
    ),
    "p4.king-must-move-then-nxa8": lambda: every_reply_then(P4, ("Nc7+",), True, "Nxa8"),
    # P5: the bishop on c1 stays shut in after 1.e3; 1.d4 opens the c1-h6 diagonal.
    "p5.e3-legal": lambda: legal(P5, "e3"),
    "p5.bishop-c1-shut-in-before": lambda: (
        not moves_from(board(P5), "c1")
        and piece(board(P5), "b2", chess.PAWN, W)
        and piece(board(P5), "d2", chess.PAWN, W)
    ),
    "p5.bishop-c1-shut-in-after-e3": lambda: not moves_from(white_to_move(board(P5, "e3")), "c1"),
    "p5.d4-legal-instead-of-e3": lambda: legal_instead(P5, "d4", "e3"),
    "p5.d4-in-the-centre": lambda: (
        board(P5).parse_san("d4").to_square in (sq("d4"), sq("e4"), sq("d5"), sq("e5"))
    ),
    "p5.d4-opens-c1-h6": lambda: all(
        attacks(board(P5, "d4"), "c1", s) for s in ("d2", "e3", "f4", "g5", "h6")
    ),
    # H1: 4.d3 defends c4 but misses 4.Qxf7#.
    "h1.d3-legal": lambda: legal(H1, "d3"),
    "h1.d3-defends-bishop-c4": lambda: sq("d3") in board(H1, "d3").attackers(W, sq("c4")),
    "h1.knight-f6-attacks-queen-h5": lambda: sq("f6") in board(H1, "d3").attackers(B, sq("h5")),
    "h1.qxf7-legal-instead-of-d3": lambda: legal_instead(H1, "Qxf7#", "d3"),
    "h1.qxf7-takes-pawn-and-mates": lambda: (
        captures(H1, "Qxf7#", "f7", chess.PAWN) and board(H1, "Qxf7#").is_checkmate()
    ),
    # H2: 1.Kf2 misses the skewer 1.Bf3+.
    "h2.kf2-legal": lambda: legal(H2, "Kf2"),
    "h2.bf3-legal-instead-of-kf2": lambda: legal_instead(H2, "Bf3+", "Kf2"),
    "h2.bf3-checks-king-d5": lambda: (
        board(H2, "Bf3+").is_check() and piece(board(H2), "d5", chess.KING, B)
    ),
    "h2.king-steps-off-then-bxb7": lambda: every_reply_then(H2, ("Bf3+",), True, "Bxb7"),
    # H3: 1.Qf7 stalemates; 1.Qf8# mates.
    "h3.qf7-legal": lambda: legal(H3, "Qf7"),
    "h3.qf7-stalemate": lambda: (
        board(H3, "Qf7").is_stalemate() and piece(board(H3), "h8", chess.KING, B)
    ),
    "h3.stalemate-is-a-draw": lambda: (
        (outcome := board(H3, "Qf7").outcome()) is not None and outcome.winner is None
    ),
    "h3.qf8-legal-instead-of-qf7": lambda: legal_instead(H3, "Qf8#", "Qf7"),
    "h3.qf8-checkmate": lambda: board(H3, "Qf8#").is_checkmate(),
    # H4: the knight on e5 is pinned; 1.f4 wins it.
    "h4.kg2-legal": lambda: legal(H4, "Kg2"),
    "h4.knight-e5-has-no-move": lambda: not moves_from(board(H4, "Kg2"), "e5"),
    "h4.knight-e5-pinned-to-king-e8": lambda: (
        board(H4).is_pinned(B, sq("e5"))
        and sq("e1") in board(H4).pin(B, sq("e5"))
        and piece(board(H4), "e8", chess.KING, B)
    ),
    "h4.f4-legal-instead-of-kg2": lambda: legal_instead(H4, "f4", "Kg2"),
    "h4.pawn-f4-attacks-e5": lambda: sq("f4") in board(H4, "f4").attackers(W, sq("e5")),
    "h4.knight-e5-lost-after-f4": lambda: every_reply_then(H4, ("f4",), False, "fxe5"),
    # H5: 1.Qc1 allows the fork 1...Ne2+; 1.Qxd4 takes the knight.
    "h5.qc1-legal": lambda: legal(H5, "Qc1"),
    "h5.ne2-follows": lambda: legal(H5, "Qc1", "Ne2+"),
    "h5.ne2-checks-and-attacks-queen-c1": lambda: (
        board(H5, "Qc1", "Ne2+").is_check() and attacks(board(H5, "Qc1", "Ne2+"), "e2", "c1")
    ),
    "h5.king-must-move-then-nxc1": lambda: every_reply_then(H5, ("Qc1", "Ne2+"), True, "Nxc1"),
    "h5.qxd4-legal-instead-of-qc1": lambda: legal_instead(H5, "Qxd4", "Qc1"),
    "h5.qxd4-takes-knight": lambda: captures(H5, "Qxd4", "d4", chess.KNIGHT),
}

CLAUSE_CLAIMS: dict[str, dict[str, list[str]]] = {
    "P1": {
        "White played 5.Nxf7": ["p1.nxf7-legal"],
        "5.Nxf7 takes the pawn on f7 and attacks the rook on h8": [
            "p1.nxf7-takes-pawn-f7",
            "p1.knight-f7-attacks-rook-h8",
        ],
        "It takes the pawn on f7 and attacks the rook on h8": [
            "p1.nxf7-takes-pawn-f7",
            "p1.knight-f7-attacks-rook-h8",
        ],
        "The knight on f7 has taken the pawn and attacks the rook on h8": [
            "p1.nxf7-takes-pawn-f7",
            "p1.knight-f7-attacks-rook-h8",
        ],
        "Black's queen on g5 already attacks the pawn on g2": ["p1.queen-g5-attacks-pawn-g2"],
        "5.Nxf7 leaves it undefended": ["p1.g2-undefended-after-nxf7"],
        "After 5.Nxf7 nothing defends the pawn on g2": ["p1.g2-undefended-after-nxf7"],
        "Black answers 5...Qxg2": ["p1.qxg2-follows"],
        "the queen then attacks the rook on h1": ["p1.queen-g2-attacks-rook-h1"],
        "The queen then attacks the rook on h1": ["p1.queen-g2-attacks-rook-h1"],
        "The queen on g2 then attacks the rook on h1": ["p1.queen-g2-attacks-rook-h1"],
        "Better was 5.Bxf7+": ["p1.bxf7-legal-instead-of-nxf7"],
        "White could have played 5.Bxf7+ instead of 5.Nxf7": ["p1.bxf7-legal-instead-of-nxf7"],
        "takes the same pawn with check": ["p1.bxf7-takes-pawn-with-check"],
        "5.Bxf7+ takes the same pawn with check": ["p1.bxf7-takes-pawn-with-check"],
        "The bishop on f7 takes the same pawn with check": ["p1.bxf7-takes-pawn-with-check"],
        "Black's king must move": ["p1.after-bxf7-only-king-moves"],
        "Black must answer the check before anything else": ["p1.black-in-check-after-bxf7"],
    },
    "P2": {
        "White played 7.Be2 to block the check from the queen on e4": [
            "p2.check-from-queen-e4",
            "p2.be2-blocks-the-check",
        ],
        "Black answers 7...Nf3": ["p2.nf3-follows"],
        "it is checkmate": ["p2.nf3-is-checkmate"],
        "The bishop on e2 is pinned and cannot take the knight on f3": [
            "p2.queen-e4-pins-bishop-e2",
            "p2.bishop-e2-cannot-take-f3",
        ],
        "The queen on e4 pins the bishop on e2 to the king": ["p2.queen-e4-pins-bishop-e2"],
        "the bishop cannot take the knight on f3": ["p2.bishop-e2-cannot-take-f3"],
        "Better was 7.Qe2": ["p2.qe2-legal-instead-of-be2"],
        "also blocks the check and attacks Black's queen on e4": [
            "p2.qe2-blocks-the-check",
            "p2.queen-e2-attacks-queen-e4",
        ],
        "7.Qe2 also blocks the check": ["p2.qe2-blocks-the-check"],
        "it attacks Black's queen on e4": ["p2.queen-e2-attacks-queen-e4"],
        "So 7.Be2 was not the only way to block the check": ["p2.be2-not-the-only-block"],
    },
    "P3": {
        "White played 1.h3": ["p3.h3-legal"],
        "It gives White's own king a free square on h2": ["p3.king-g1-can-step-to-h2"],
        "Now the king on g1 can step to h2": ["p3.king-g1-can-step-to-h2"],
        "1.h3 only gives White's own king a free square": ["p3.h3-frees-only-h2"],
        "Black's king on g8 is shut in by its own pawns on f7, g7 and h7": [
            "p3.black-king-g8-shut-in-by-f7-g7-h7"
        ],
        "Better was 1.Rd8": ["p3.rd8-legal-instead-of-h3"],
        "White could have played 1.Rd8 instead of 1.h3": ["p3.rd8-legal-instead-of-h3"],
        "is checkmate": ["p3.rd8-is-checkmate"],
        "1.Rd8 is checkmate at once": ["p3.rd8-is-checkmate"],
        "The rook on d8 gives checkmate at once": ["p3.rd8-is-checkmate"],
    },
    "P4": {
        "White played 1.Kd2": ["p4.kd2-legal"],
        "Better was 1.Nc7+": ["p4.nc7-legal-instead-of-kd2"],
        "White could have played 1.Nc7+ instead of 1.Kd2": ["p4.nc7-legal-instead-of-kd2"],
        "gives check and also attacks the rook on a8": ["p4.nc7-check-and-attacks-rook-a8"],
        "1.Nc7+ gives check and attacks the rook on a8 at the same time": [
            "p4.nc7-check-and-attacks-rook-a8"
        ],
        "The knight on c7 gives check and attacks the rook on a8 at the same time": [
            "p4.nc7-check-and-attacks-rook-a8"
        ],
        "After Black's king moves, the knight takes the rook on a8": [
            "p4.king-must-move-then-nxa8"
        ],
        "Black's king must move": ["p4.king-must-move-then-nxa8"],
        "then the knight takes the rook on a8": ["p4.king-must-move-then-nxa8"],
        "then the knight on c7 takes the rook on a8": ["p4.king-must-move-then-nxa8"],
    },
    "P5": {
        "White played 1.e3": ["p5.e3-legal"],
        "After 1.e3 the bishop on c1 still has no free square": ["p5.bishop-c1-shut-in-after-e3"],
        "the pawns on b2 and d2 block it": [
            "p5.bishop-c1-shut-in-before",
            "p5.bishop-c1-shut-in-after-e3",
        ],
        "The bishop on c1 was already shut in by the pawns on b2 and d2 before 1.e3": [
            "p5.bishop-c1-shut-in-before"
        ],
        "1.e3 does not free it": ["p5.bishop-c1-shut-in-after-e3"],
        "Better was 1.d4": ["p5.d4-legal-instead-of-e3"],
        "White could have played 1.d4 instead of 1.e3": ["p5.d4-legal-instead-of-e3"],
        "puts a pawn in the centre and opens a diagonal for the bishop on c1": [
            "p5.d4-in-the-centre",
            "p5.d4-opens-c1-h6",
        ],
        "1.d4 puts a pawn in the centre and opens a diagonal for the bishop on c1": [
            "p5.d4-in-the-centre",
            "p5.d4-opens-c1-h6",
        ],
        "The pawn on d4 stands in the centre": ["p5.d4-in-the-centre"],
        "the bishop on c1 can then use the diagonal from c1 to h6": ["p5.d4-opens-c1-h6"],
    },
    "H1": {
        "White played 4.d3": ["h1.d3-legal"],
        "The pawn on d3 defends the bishop on c4": ["h1.d3-defends-bishop-c4"],
        "Black's knight on f6 attacks White's queen on h5": ["h1.knight-f6-attacks-queen-h5"],
        "White could have played 4.Qxf7# instead of 4.d3": ["h1.qxf7-legal-instead-of-d3"],
        "The queen takes the pawn on f7 and gives checkmate": ["h1.qxf7-takes-pawn-and-mates"],
    },
    "H2": {
        "White played 1.Kf2": ["h2.kf2-legal"],
        "White could have played 1.Bf3+ instead of 1.Kf2": ["h2.bf3-legal-instead-of-kf2"],
        "The bishop on f3 gives check to the king on d5": ["h2.bf3-checks-king-d5"],
        "Black's king must step off the diagonal": ["h2.king-steps-off-then-bxb7"],
        "then the bishop on f3 can take the rook on b7": ["h2.king-steps-off-then-bxb7"],
    },
    "H3": {
        "White played 1.Qf7": ["h3.qf7-legal"],
        "Black's king on h8 is not in check and has no move": ["h3.qf7-stalemate"],
        "it is stalemate": ["h3.qf7-stalemate"],
        "The game ends in a draw": ["h3.stalemate-is-a-draw"],
        "White could have played 1.Qf8# instead of 1.Qf7": ["h3.qf8-legal-instead-of-qf7"],
        "The queen on f8 gives checkmate": ["h3.qf8-checkmate"],
    },
    "H4": {
        "White played 1.Kg2": ["h4.kg2-legal"],
        "Black's knight on e5 cannot move": ["h4.knight-e5-has-no-move"],
        "the rook on e1 pins it to the king on e8": ["h4.knight-e5-pinned-to-king-e8"],
        "White could have played 1.f4 instead of 1.Kg2": ["h4.f4-legal-instead-of-kg2"],
        "The pawn on f4 attacks the pinned knight on e5": [
            "h4.pawn-f4-attacks-e5",
            "h4.knight-e5-pinned-to-king-e8",
        ],
        "Black cannot save the knight": ["h4.knight-e5-lost-after-f4"],
    },
    "H5": {
        "White played 1.Qc1": ["h5.qc1-legal"],
        "Black answers 1...Ne2+": ["h5.ne2-follows"],
        "The knight on e2 gives check to the king on g1 and attacks the queen on c1": [
            "h5.ne2-checks-and-attacks-queen-c1"
        ],
        "White's king must move": ["h5.king-must-move-then-nxc1"],
        "then the knight on e2 can take the queen on c1": ["h5.king-must-move-then-nxc1"],
        "White could have played 1.Qxd4 instead of 1.Qc1": ["h5.qxd4-legal-instead-of-qc1"],
        "The queen takes the knight on d4": ["h5.qxd4-takes-knight"],
    },
}


def all_facts() -> list[tuple[str, str]]:
    """(test, fact) for every fact of every run version."""
    out = [(t, f) for t, facts in data.TESTS_V1.items() for f in facts]
    for tests in (data.TESTS_RUN4, data.TESTS_RUN5, data.TESTS_HELDOUT):
        out += [(t, f) for t, facts in tests.items() for _, f in facts]
    return out


def all_clauses() -> list[tuple[str, str, str]]:
    """(test, fact, clause) for every clause of every fact."""
    return [(t, f, c) for t, f in all_facts() for c in clauses(f)]


def claims_of(test: str, clause: str) -> list[str]:
    return CLAUSE_CLAIMS.get(test.split("-")[0], {}).get(clause, [])
