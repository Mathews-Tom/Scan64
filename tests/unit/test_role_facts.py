"""Role-labelled narrator facts (#460): python-chess proves every relation.

The 10 positions are the #457 experiment set (P1-P5) and the #462 held-out
set (H1-H5). ``tests/fixtures/role_facts.json`` is the reviewed snapshot;
regenerate it with ``uv run python tests/unit/test_role_facts.py`` and review
the diff.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import chess
import pytest

from scan64.explanations.role_facts import (
    ROLES,
    PieceOnSquare,
    Relation,
    RoleFact,
    RoleFactsError,
    direction,
    role_facts,
    role_label,
)

SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "role_facts.json"

# (name, FEN, played, best, best line, line after the played move). Lines are
# the forcing continuations the request on #460 names.
FIXTURES: tuple[tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "P1",
        "r1b1kbnr/pppp1ppp/8/4N1q1/2BnP3/8/PPPP1PPP/RNBQK2R w KQkq - 1 5",
        "e5f7",
        "c4f7",
        ("c4f7", "e8e7"),
        ("g5g2",),
    ),
    (
        "P2",
        "r1b1kbnr/pppp1Npp/8/8/2Bnq3/8/PPPP1P1P/RNBQKR2 w Qkq - 0 7",
        "c4e2",
        "d1e2",
        ("d1e2",),
        ("d4f3",),
    ),
    ("P3", "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "h2h3", "d1d8", ("d1d8",), ()),
    (
        "P4",
        "r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1",
        "e1d2",
        "d5c7",
        ("d5c7", "e8d8", "c7a8"),
        (),
    ),
    (
        "P5",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "e2e3",
        "d2d4",
        ("d2d4",),
        (),
    ),
    (
        "H1",
        "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
        "d2d3",
        "h5f7",
        ("h5f7",),
        (),
    ),
    (
        "H2",
        "8/1r6/8/3k4/8/8/6B1/6K1 w - - 0 1",
        "g1f2",
        "g2f3",
        ("g2f3", "d5c5", "f3b7"),
        (),
    ),
    ("H3", "7k/8/6K1/8/8/8/8/5Q2 w - - 0 1", "f1f7", "f1f8", ("f1f8",), ()),
    ("H4", "4k3/8/8/4n3/8/8/5P2/4R1K1 w - - 0 1", "g1g2", "f2f4", ("f2f4",), ()),
    (
        "H5",
        "6k1/8/8/8/3n4/8/6PP/3Q2K1 w - - 0 1",
        "d1c1",
        "d1d4",
        ("d1d4",),
        ("d4e2", "g1f1", "e2c1"),
    ),
)
NAMES = [fixture[0] for fixture in FIXTURES]

# Factor names (#406) a fact must never carry; numbers of pawns and centipawns.
FACTOR_WORDS = (
    "king safety",
    "king danger",
    "mobility",
    "imbalance",
    "winnable",
    "threats",
    "passed file",
)
NUMBER_OF_PAWNS = re.compile(r"\d+(?:\.\d+)?\s*pawns?\b|centipawn|\bcp\b", re.IGNORECASE)
BANNED_PHRASES = ("at the same time", "covers", "covered")


def _facts(name: str) -> tuple[RoleFact, ...]:
    _, fen, played, best, best_line, played_line = next(f for f in FIXTURES if f[0] == name)
    return role_facts(fen, played, best, best_line=best_line, played_line=played_line)


def _snapshot_entry(name: str) -> list[dict[str, object]]:
    fen = next(f for f in FIXTURES if f[0] == name)[1]
    mover = chess.Board(fen).turn
    return [
        {
            "role": role_label(fact.role, mover),
            "fact": fact.text,
            "relations": [_relation_record(relation) for relation in fact.relations],
        }
        for fact in _facts(name)
    ]


def _piece_record(piece: PieceOnSquare | None) -> str | None:
    if piece is None:
        return None
    colour = chess.COLOR_NAMES[piece.colour]
    return f"{colour} {chess.piece_name(piece.piece)} {chess.square_name(piece.square)}"


def _relation_record(relation: Relation) -> dict[str, object]:
    record: dict[str, object] = {"kind": relation.kind, "move": relation.move}
    for name in ("subject", "target", "through"):
        if (piece := _piece_record(getattr(relation, name))) is not None:
            record[name] = piece
    if relation.direction is not None:
        record["direction"] = relation.direction
    if relation.square is not None:
        record["square"] = chess.square_name(relation.square)
    if relation.reply is not None:
        record["reply"] = relation.reply
    if relation.already:
        record["already"] = True
    return record


def _nearest_beyond(board: chess.Board, square: int, king: int) -> int | None:
    """The first occupied square past ``square`` on the line from ``king``."""
    df = (chess.square_file(square) > chess.square_file(king)) - (
        chess.square_file(square) < chess.square_file(king)
    )
    dr = (chess.square_rank(square) > chess.square_rank(king)) - (
        chess.square_rank(square) < chess.square_rank(king)
    )
    f, r = chess.square_file(square) + df, chess.square_rank(square) + dr
    while 0 <= f < 8 and 0 <= r < 8:
        if board.piece_at(chess.square(f, r)) is not None:
            return chess.square(f, r)
        f, r = f + df, r + dr
    return None


def _board(relation: Relation) -> tuple[chess.Board, chess.Move, chess.Board]:
    before = chess.Board(relation.fen)
    move = chess.Move.from_uci(relation.move)
    assert move in before.legal_moves
    after = before.copy()
    after.push(move)
    return before, move, after


def _holds(relation: Relation) -> None:
    """Prove ``relation`` with python-chess on its own position and move."""
    before, move, after = _board(relation)
    subject, target, through = relation.subject, relation.target, relation.through

    def on(board: chess.Board, piece: object) -> None:
        assert piece is not None
        found = board.piece_at(piece.square)  # type: ignore[attr-defined]
        assert found is not None
        assert (found.color, found.piece_type) == (piece.colour, piece.piece)  # type: ignore[attr-defined]

    match relation.kind:
        case "moves":
            on(after, subject)
            assert subject is not None and subject.square == move.to_square
            if relation.direction is not None:
                assert direction(before.turn, move) == relation.direction
        case "takes":
            assert before.is_capture(move)
            on(before, target)
            assert target is not None and target.colour != before.turn
            assert target.piece != chess.KING
            taken = (
                move.to_square
                if not before.is_en_passant(move)
                else chess.square(
                    chess.square_file(move.to_square), chess.square_rank(move.from_square)
                )
            )
            assert target.square == taken
            assert subject is not None and subject.square == move.to_square
            on(after, subject)
        case "attacks":
            on(after, subject)
            on(after, target)
            assert subject is not None and target is not None
            assert target.square in after.attacks(subject.square)
            assert target.colour != subject.colour
            if relation.already:
                # The same attacker on the same square already attacked the same piece.
                on(before, subject)
                on(before, target)
                assert target.square in before.attacks(subject.square)
        case "gives_check":
            assert after.is_check()
            assert subject is not None and subject.square in after.checkers()
            on(after, target)
            assert target is not None and after.king(not before.turn) == target.square
        case "mates":
            assert after.is_checkmate()
            on(after, subject)
            assert subject is not None and subject.square == move.to_square
            on(after, target)
            assert target is not None and after.king(not before.turn) == target.square
        case "stalemates":
            assert after.is_stalemate()
            on(after, subject)
            assert subject is not None and subject.square == move.to_square
        case "draws":
            assert after.is_insufficient_material() and not after.is_checkmate()
            on(after, subject)
            assert subject is not None and subject.square == move.to_square
        case "blocks":
            assert before.is_check() and through is not None
            assert through.square in before.checkers()
            on(before, through)
            king = before.king(before.turn)
            assert king is not None and len(before.checkers()) == 1
            assert move.to_square in chess.SquareSet.between(through.square, king)
            assert not before.is_en_passant(move)
            assert subject is not None and subject.square == move.to_square
            on(after, subject)
            assert before.piece_type_at(move.from_square) != chess.KING
        case "pins":
            assert subject is not None and target is not None and through is not None
            on(after, subject)
            on(after, target)
            assert after.is_pinned(target.colour, target.square)
            assert through.piece == chess.KING and after.king(target.colour) == through.square
            # The pinner is the first piece behind the pinned one, seen from its king.
            assert _nearest_beyond(after, target.square, through.square) == subject.square
        case "frees":
            on(before, target)
            assert target is not None
            scratch_before = before.copy()
            scratch_before.turn = target.colour
            scratch_after = after.copy()
            scratch_after.turn = target.colour
            assert not any(
                m.from_square == target.square for m in scratch_before.pseudo_legal_moves
            )
            assert any(m.from_square == target.square for m in scratch_after.pseudo_legal_moves)
        case "defends":
            on(after, subject)
            on(after, target)
            assert subject is not None and target is not None
            assert target.colour == subject.colour
            assert target.square in after.attacks(subject.square)
            assert not before.attackers(target.colour, target.square)
        case "undefended":
            on(after, target)
            assert target is not None and not after.attackers(target.colour, target.square)
        case "shuts_in":
            assert subject is not None and target is not None
            board = after if subject.piece == chess.KING else before
            on(board, subject)
            on(board, target)
            assert target.colour == subject.colour
            assert target.square in board.attacks(subject.square)
            if subject.piece != chess.KING:
                scratch = board.copy()
                scratch.turn = subject.colour
                assert not any(m.from_square == subject.square for m in scratch.legal_moves)
            else:
                # A king shut in for a mate: without this piece it is no mate.
                assert after.is_checkmate()
                without = after.copy()
                without.remove_piece_at(target.square)
                assert not without.is_checkmate()
        case "opens":
            assert subject is not None and relation.square is not None
            assert subject.piece == chess.KING
            on(before, subject)

            def steps(board: chess.Board) -> set[int]:
                scratch = board.copy()
                scratch.turn = subject.colour
                return {m.to_square for m in scratch.legal_moves if m.from_square == subject.square}

            assert relation.square in steps(after) - steps(before)
        case "prevents":
            assert after.is_check() and not after.is_game_over()
            on(before, target)
            on(after, target)
            assert target is not None and target.colour != before.turn
            assert target.piece != chess.KING
            assert relation.reply is not None and relation.reply_fen is not None
            reply = chess.Move.from_uci(relation.reply)
            # The reply was legal after the played move: one legal move from ``fen``
            # reaches ``reply_fen``, and the same piece plays it there.
            threatened = chess.Board(relation.reply_fen)
            assert reply in threatened.legal_moves
            reached = set()
            for legal in before.legal_moves:
                scratch = before.copy()
                scratch.push(legal)
                reached.add(scratch.fen())
            assert relation.reply_fen in reached
            on(threatened, target)
            assert reply.from_square == target.square
            # Pseudo-legal, no pin forbids it, and still illegal: the check stops it.
            assert after.is_pseudo_legal(reply) and reply not in after.legal_moves
            assert reply.to_square in after.pin(target.colour, target.square)
            # A capturing reply names the piece it takes, still on its square.
            if through is None:
                assert after.piece_at(reply.to_square) is None
            else:
                on(after, through)
                assert through.square == reply.to_square and through.colour != target.colour
        case _:
            pytest.fail(f"Unchecked relation {relation.kind}")


@pytest.mark.parametrize("name", NAMES)
def test_every_relation_holds_on_the_board(name: str) -> None:
    facts = _facts(name)
    assert facts
    for fact in facts:
        assert fact.relations, fact
        for relation in fact.relations:
            _holds(relation)


@pytest.mark.parametrize("name", NAMES)
def test_facts_use_the_wording_rules(name: str) -> None:
    roles = [fact.role for fact in _facts(name)]
    # Roles keep the story order: the move, what it misses, what follows, the alternative.
    assert roles == sorted(roles, key=ROLES.index)
    for fact in _facts(name):
        text = fact.text.lower()
        assert not any(phrase in text for phrase in BANNED_PHRASES), fact.text
        assert not any(word in text for word in FACTOR_WORDS), fact.text
        assert NUMBER_OF_PAWNS.search(fact.text) is None, fact.text


def test_the_facts_match_the_reviewed_snapshot() -> None:
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert {name: _snapshot_entry(name) for name in NAMES} == expected


def test_taken_and_attacked_stay_apart() -> None:
    # P1: 5.Nxf7 takes the pawn and only attacks the rook and the queen.
    facts = _facts("P1")[:3]
    assert [r.kind for fact in facts for r in fact.relations] == ["takes", "attacks", "attacks"]
    assert "takes the pawn on f7" in facts[0].text
    assert all("takes" not in fact.text for fact in facts[1:])


def test_a_check_comes_before_the_capture_that_follows_it() -> None:
    text = " ".join(fact.text for fact in _facts("P4"))
    assert "1.Nc7+ gives check; after the king moves, the knight on c7 takes the rook on a8" in text
    follows = " ".join(fact.text for fact in _facts("H5") if fact.role == "what_follows")
    assert "1...Ne2+ gives check; after the king moves, the knight on e2 takes the queen on c1" in (
        follows
    )


def test_directions_are_from_the_movers_side() -> None:
    assert "the king on g1 steps forward from g1 to g2" in _facts("H4")[0].text
    assert "the bishop on c4 goes back from c4 to e2" in _facts("P2")[0].text
    assert "the queen on d1 goes sideways from d1 to c1" in _facts("H5")[0].text
    black = chess.Board("4k3/8/8/8/8/8/8/4K3 b - - 0 1")
    assert direction(black.turn, chess.Move.from_uci("e8e7")) == "forward"


def test_the_game_result_is_stated() -> None:
    stalemate = " ".join(fact.text for fact in _facts("H3") if fact.role == "the_move")
    assert "stalemate" in stalemate and "ends at once as a draw" in stalemate
    mate = " ".join(fact.text for fact in _facts("H3") if fact.role == "could_have_played")
    assert "gives checkmate to the king on h8: the game ends, and White wins" in mate


def test_a_judgement_on_the_block_and_the_alternative() -> None:
    texts = [fact.text for fact in _facts("P2")]
    assert (
        "7.Be2 was not the only way to block the check: "
        "7.Qe2 also blocks the check from the queen on e4." in texts
    )
    assert "White could have played 7.Qe2 instead of 7.Be2." in texts


def test_a_pin_names_what_it_pins_to() -> None:
    misses = [fact.text for fact in _facts("H4") if fact.role == "what_it_misses"]
    assert misses == [
        "Black's knight on e5 cannot move, because the rook on e1 pins the knight on e5 "
        "to the king on e8."
    ]
    # P2: the played move walks into a pin, and the pin decides what follows.
    # The pinner is the piece in front of the pinned one, not one behind its king.
    texts = [f.text for f in role_facts("4R3/8/8/4n3/4k3/8/4BP2/6K1 w - - 0 1", "g1h1", "f2f4")]
    assert (
        "Black's knight on e5 cannot move, because the rook on e8 pins the knight on e5 "
        "to the king on e4." in (texts)
    )
    # A rook moving along its own pin was already pinned; the attack is not "already".
    texts = [f.text for f in role_facts("4k3/4r3/8/8/8/8/4R3/4K3 w - - 0 1", "e2e3", "e1d1")]
    assert not any("pins" in text for text in texts)
    assert "Black's rook on e7 attacks White's rook on e3." in texts
    texts = [fact.text for fact in _facts("P2")]
    assert "Black's queen on e4 pins the bishop on e2 to the king on e1." in texts
    assert (
        "The bishop on e2 cannot take the knight on f3: the queen on e4 pins the bishop on e2 "
        "to the king on e1." in texts
    )


def test_what_the_move_leaves_attacked() -> None:
    misses = [fact.text for fact in _facts("P1") if fact.role == "what_it_misses"]
    assert misses == [
        "Black's queen on g5 already attacks White's pawn on g2.",
        "After 5.Nxf7 nothing defends the pawn on g2.",
    ]
    alternative = [fact.text for fact in _facts("P1") if fact.role == "could_have_played"]
    assert alternative[-1] == (
        "After 5.Bxf7+, Black must answer the check, so the queen on g5 cannot play "
        "5...Qxg2 at once."
    )


def test_shut_in_pieces_are_named_with_what_shuts_them_in() -> None:
    texts = [fact.text for fact in _facts("P3")]
    assert "Black's king on g8 is shut in by its own pawns on f7, g7 and h7." in texts
    assert "Now the king on g1 can step to h2." in texts
    texts = [fact.text for fact in _facts("P5")]
    assert (
        "The bishop on c1 was already shut in by the pawns on b2 and d2 before 1.e3, "
        "and 1.e3 does not free the bishop on c1."
    ) in texts
    assert "1.d4 frees the bishop on c1." in texts


def test_a_move_that_defends_a_piece_says_so() -> None:
    assert "The pawn on d3 defends the bishop on c4." in [f.text for f in _facts("H1")]


def test_en_passant_takes_the_checker_and_does_not_block() -> None:
    facts = role_facts("4k3/8/8/3pP3/4K3/8/8/8 w - d6 0 1", "e5d6", "e4f3")
    assert facts[0].text == "1.exd6: the pawn on e5 takes the pawn on d5."
    assert not any(r.kind == "blocks" for fact in facts for r in fact.relations)


def test_a_mate_later_in_a_line_is_stated() -> None:
    facts = role_facts(
        "7k/8/8/8/8/8/1R6/R7 w - - 0 1", "a1a2", "b2b7", best_line=("b2b7", "h8g8", "a1a8")
    )
    assert "The line goes on to 2.Ra8#, checkmate: the game ends, and White wins." in [
        f.text for f in facts
    ]
    facts = role_facts(
        "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2",
        "a7a6",
        "d8h4",
        played_line=("a2a3", "d8h4"),
    )
    follows = [f.text for f in facts if f.role == "what_follows"]
    assert follows[-1] == "The line goes on to 3...Qh4#, checkmate: the game ends, and Black wins."
    # After a mate there is no check to answer.
    assert not any(r.kind == "prevents" for fact in facts for r in fact.relations)
    for fact in facts:
        for relation in fact.relations:
            _holds(relation)


def test_cannot_take_only_when_the_pin_forbids_the_capture() -> None:
    # The pinner leaves the line with the reply, so the capture is legal.
    facts = role_facts(
        "r2qkb1r/pb3p1p/n1BppQ1p/2P2n2/1p6/3P2P1/PP2PP1P/RN2K1NR b KQkq - 0 11",
        "d8d7",
        "d8d7",
        best_line=("d8d7", "f2f3"),
        played_line=("c6b7", "f5d4", "f6g6", "e6e5"),
    )
    assert not any("cannot take" in fact.text for fact in facts)
    # The pinned rook takes along the pin line.
    facts = role_facts("4r1k1/8/8/8/2n5/8/R7/4K3 w - - 0 1", "a2e2", "e1d1", played_line=("c4e5",))
    assert not any("cannot take" in fact.text for fact in facts)


def test_a_stalemate_later_in_a_line_is_stated() -> None:
    facts = role_facts(
        "8/8/8/8/2Qp4/K7/8/k7 w - - 0 1", "c4g8", "c4a6", best_line=("c4a6", "d4d3", "a6d3")
    )
    assert any(
        "and it is stalemate: Black has no legal move and is not in check, so the game ends "
        "at once as a draw." in fact.text
        for fact in facts
    )
    for fact in facts:
        for relation in fact.relations:
            _holds(relation)


@pytest.mark.parametrize(
    ("fen", "played", "best", "best_line", "played_line"),
    [
        # The better move takes the piece that would have played the threat.
        ("8/5K1b/8/b3R3/k4rR1/8/4p3/2n2b2 w - - 0 1", "f7e8", "g4f4", ("g4f4",), ("f4g4",)),
        # The better move's own piece blocks the threat's path.
        (
            "5b2/1q3Q2/2r2n2/5b2/k3Pnr1/8/8/K7 w - - 0 1",
            "f7f6",
            "f7c4",
            ("f7c4",),
            ("c6c2",),
        ),
        # The better move moves the piece the threat would have taken, with check.
        (
            "r1b1kbnr/ppp1q1pp/3pp3/5p2/2Bn1P2/P3PN1P/1PPP1KP1/RNBQ3R b kq - 3 7",
            "e7g5",
            "e7h4",
            ("e7h4",),
            ("f3g5",),
        ),
        # The threat is a king move to an attacked square.
        (
            "8/N1n5/8/8/7B/2k3N1/8/1K6 w - - 0 1",
            "g3e2",
            "g3e4",
            ("g3e4", "c3c4"),
            ("c3d2",),
        ),
    ],
)
def test_must_answer_the_check_only_when_the_check_stops_the_threat(
    fen: str, played: str, best: str, best_line: tuple[str, ...], played_line: tuple[str, ...]
) -> None:
    facts = role_facts(fen, played, best, best_line=best_line, played_line=played_line)
    assert not any("must answer the check" in fact.text for fact in facts)
    for fact in facts:
        for relation in fact.relations:
            _holds(relation)


def test_cannot_take_is_not_blamed_on_the_pin_when_a_check_stops_it() -> None:
    facts = role_facts("4r1k1/8/8/8/7b/6r1/R7/4K3 w - - 0 1", "a2e2", "e1d1", played_line=("g3e3",))
    assert not any("cannot take" in fact.text for fact in facts)


def test_illegal_input_fails_fast() -> None:
    fen = chess.STARTING_FEN
    with pytest.raises(RoleFactsError, match="not legal"):
        role_facts(fen, "e2e5", "d2d4")
    with pytest.raises(RoleFactsError, match="starts with"):
        role_facts(fen, "e2e3", "d2d4", best_line=("e2e4",))
    with pytest.raises(RoleFactsError, match="not legal"):
        role_facts(fen, "e2e3", "d2d4", played_line=("e7e4",))


def test_shut_in_is_stated_only_for_pawns_the_mate_needs() -> None:
    # 1...Qa7# mates on the a-file; the pawn on b2 takes no part in it.
    facts = role_facts("8/2q5/8/8/5p2/8/1P6/K1k1q3 b - - 0 1", "c7c8", "c7a7")
    assert not any("shut in" in fact.text for fact in facts)
    # The back-rank mate needs every pawn in front of the king.
    texts = [fact.text for fact in _facts("P3")]
    assert "Black's king on g8 is shut in by its own pawns on f7, g7 and h7." in texts


def test_cannot_move_is_not_blamed_on_the_pin_when_a_check_also_holds_it() -> None:
    # 1...Qxb5+ pins the rook on c6 and checks: the check also stops its moves along the pin.
    facts = role_facts("4qn2/2r2k2/2Q2B2/1BK5/8/B1R4n/5p2/1q6 b - - 0 1", "f7g6", "b1b5")
    pins = [fact.text for fact in facts if "because" in fact.text]
    assert pins and all("cannot move" not in text for text in pins), pins


def test_pin_and_check_sentences_name_the_piece_not_a_pronoun() -> None:
    for name in NAMES:
        for fact in _facts(name):
            if any(
                r.kind in ("pins", "blocks", "mates", "shuts_in", "frees") for r in fact.relations
            ):
                assert re.search(r"\bit\b", fact.text) is None, fact.text


def test_a_draw_by_insufficient_material_in_a_line_is_stated() -> None:
    texts = [fact.text for fact in _facts("P4")]
    assert (
        "The line goes on to 2.Nxa8, and the game is drawn: neither side has enough "
        "material left to give mate." in texts
    )


def test_relations_hold_on_random_positions() -> None:
    """Property check: on seeded random positions every stated relation holds."""
    import random

    rnd = random.Random(476)
    checked = 0
    while checked < 300:
        board = chess.Board(None)
        for square in rnd.sample(range(64), rnd.randint(2, 12)):
            board.set_piece_at(
                square, chess.Piece(rnd.choice([1, 2, 3, 4, 5, 5]), rnd.random() < 0.5)
            )
        board.set_piece_at(
            rnd.choice([s for s in range(64) if not board.piece_at(s)]),
            chess.Piece(chess.KING, chess.WHITE),
        )
        board.set_piece_at(
            rnd.choice([s for s in range(64) if not board.piece_at(s)]),
            chess.Piece(chess.KING, chess.BLACK),
        )
        board.turn = rnd.random() < 0.5
        if not board.is_valid() or not any(board.legal_moves):
            continue
        moves = list(board.legal_moves)
        played, best = rnd.choice(moves), rnd.choice(moves)
        line = [best]
        scratch = board.copy()
        scratch.push(best)
        for _ in range(rnd.randint(0, 3)):
            if not any(scratch.legal_moves):
                break
            line.append(rnd.choice(list(scratch.legal_moves)))
            scratch.push(line[-1])
        after = board.copy()
        after.push(played)
        follow = [rnd.choice(list(after.legal_moves))] if any(after.legal_moves) else []
        facts = role_facts(
            board.fen(),
            played.uci(),
            best.uci(),
            best_line=tuple(m.uci() for m in line),
            played_line=tuple(m.uci() for m in follow),
        )
        for fact in facts:
            for relation in fact.relations:
                _holds(relation)
        checked += 1


if __name__ == "__main__":
    SNAPSHOT.write_text(
        json.dumps({name: _snapshot_entry(name) for name in NAMES}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {SNAPSHOT}")
