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
            "relations": [relation.kind for relation in fact.relations],
        }
        for fact in _facts(name)
    ]


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
            on(after, subject)
        case "attacks":
            on(after, subject)
            on(after, target)
            assert subject is not None and target is not None
            assert target.square in after.attacks(subject.square)
            assert target.colour != subject.colour
        case "gives_check":
            assert after.is_check()
            assert subject is not None and subject.square in after.checkers()
            on(after, target)
            assert target is not None and after.king(not before.turn) == target.square
        case "mates":
            assert after.is_checkmate()
            on(after, target)
            assert target is not None and after.king(not before.turn) == target.square
        case "stalemates":
            assert after.is_stalemate()
        case "blocks":
            assert before.is_check() and through is not None
            assert through.square in before.checkers()
            assert not after.was_into_check()
            assert move.to_square != through.square
            assert before.piece_type_at(move.from_square) != chess.KING
        case "pins":
            assert subject is not None and target is not None and through is not None
            on(after, subject)
            on(after, target)
            assert after.is_pinned(target.colour, target.square)
            assert subject.square in after.pin(target.colour, target.square)
            assert through.piece == chess.KING and after.king(target.colour) == through.square
        case "frees":
            assert target is not None
            scratch_before = before.copy()
            scratch_before.turn = target.colour
            scratch_after = after.copy()
            scratch_after.turn = target.colour
            assert not any(
                m.from_square == target.square for m in scratch_before.pseudo_legal_moves
            )
            assert any(m.from_square == target.square for m in scratch_after.pseudo_legal_moves)
        case "prevents":
            assert after.is_check()
            on(before, target)
            assert target is not None and target.colour != before.turn
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
    mate = " ".join(fact.text for fact in _facts("H3") if fact.role == "what_it_misses")
    assert "it is mate: the game ends, and White wins" in mate


def test_a_judgement_on_the_block_and_the_alternative() -> None:
    texts = [fact.text for fact in _facts("P2")]
    assert "7.Be2 was not the only way to block the check: 7.Qe2 also blocks it." in texts
    assert "White could have played 7.Qe2 instead of 7.Be2." in texts


def test_a_pin_names_what_it_pins_to() -> None:
    texts = [fact.text for fact in _facts("H4")]
    assert any("pinned to the king on e8 by the rook on e1" in text for text in texts)


def test_illegal_input_fails_fast() -> None:
    fen = chess.STARTING_FEN
    with pytest.raises(RoleFactsError, match="not legal"):
        role_facts(fen, "e2e5", "d2d4")
    with pytest.raises(RoleFactsError, match="starts with"):
        role_facts(fen, "e2e3", "d2d4", best_line=("e2e4",))
    with pytest.raises(RoleFactsError, match="not legal"):
        role_facts(fen, "e2e3", "d2d4", played_line=("e7e4",))


if __name__ == "__main__":
    SNAPSHOT.write_text(
        json.dumps({name: _snapshot_entry(name) for name in NAMES}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {SNAPSHOT}")
