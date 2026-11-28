"""Chess concept library (#459): one positive and one negative position per concept."""

from __future__ import annotations

import re

import chess
import pytest

from scan64.explanations.concepts import (
    ConceptFact,
    compensation,
    initiative,
    intent_concepts,
    move_concepts,
    position_concepts,
    prophylaxis,
    sacrifice,
    static_exchange,
)

# (concept, FEN, square the fact names) — every FEN is a legal position.
POSITION_POSITIVE = [
    ("hanging_piece", "4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1", "d5"),
    ("trapped_piece", "r5k1/B1p5/1p6/8/8/8/8/6K1 w - - 0 1", "a7"),
    ("back_rank_weakness", "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1", "g8"),
    ("overloading", "k7/3q4/8/1n6/8/7b/8/1R4KR w - - 0 1", "d7"),
    # A king can be overloaded, and a rook trade that wins a pawn counts (#488).
    ("overloading", "3rk3/5p2/8/8/2B5/8/8/3RK3 w - - 0 1", "e8"),
    ("passed_pawn", "4k3/8/8/3P4/8/8/8/4K3 w - - 0 1", "d5"),
    ("isolated_pawn", "4k3/8/8/8/3P4/8/PP6/4K3 w - - 0 1", "d4"),
    ("doubled_pawns", "4k3/8/8/8/8/2P5/2P5/4K3 w - - 0 1", "c3"),
    ("backward_pawn", "4k3/8/8/2p5/2P1P3/3P4/8/4K3 w - - 0 1", "d3"),
    ("blockade", "4k3/8/3n4/3P4/8/8/8/4K3 w - - 0 1", "d6"),
    ("outpost", "4k3/8/8/3N4/4P3/8/8/4K3 w - - 0 1", "d5"),
    ("weak_square", "4k3/8/8/8/4P3/8/8/4K3 w - - 0 1", "d5"),
    ("bad_bishop", "4k3/8/4p3/3pP3/3P4/8/8/2B1K3 w - - 0 1", "c1"),
    ("open_file", "6k1/pppp1ppp/8/8/8/8/PPPP1PPP/4RK2 w - - 0 1", "e1"),
    ("half_open_file", "4k3/pppppppp/8/8/8/8/PPPP1PPP/4RK2 w - - 0 1", "e1"),
    (
        "space",
        "r1bqkb1r/pp1n1ppp/2n1p3/2ppP3/3P1P2/2P2N2/PP4PP/RNBQKB1R b KQkq - 0 1",
        "e5",
    ),
]

POSITION_NEGATIVE = [
    ("hanging_piece", "4k3/8/4p3/3n4/8/8/8/3RK3 w - - 0 1"),  # defended, rook attacks
    ("trapped_piece", "r5k1/B7/8/8/8/8/8/6K1 w - - 0 1"),  # the bishop can escape
    ("back_rank_weakness", "6k1/5pp1/7p/8/8/8/8/4R1K1 w - - 0 1"),  # h7 is free
    ("overloading", "k7/3q4/8/1n6/6p1/7b/8/1R4KR w - - 0 1"),  # two defenders
    ("overloading", "k7/3q4/8/1n6/8/7b/1R5R/1Q4KR w - - 0 1"),  # the recapture loses the queen
    # A queen trade that wins only a pawn is no overload (#488).
    ("overloading", "r1bqk2r/p4ppp/8/4p3/1nB5/8/PPP2PPP/R2QK2R w KQkq - 0 12"),
    ("passed_pawn", "4k3/2p5/8/3P4/8/8/8/4K3 w - - 0 1"),
    ("isolated_pawn", "4k3/8/8/8/3P4/4P3/8/4K3 w - - 0 1"),
    ("doubled_pawns", "4k3/8/8/8/8/3P4/2P5/4K3 w - - 0 1"),
    ("backward_pawn", "4k3/8/8/8/2P1P3/3P4/8/4K3 w - - 0 1"),  # no pawn controls d4
    ("blockade", "4k3/8/4n3/3P4/8/8/8/4K3 w - - 0 1"),
    ("outpost", "4k3/2p5/8/3N4/4P3/8/8/4K3 w - - 0 1"),  # c7-c6 can drive it away
    ("weak_square", "4k3/2p1pp2/8/8/4P3/8/8/4K3 w - - 0 1"),
    ("bad_bishop", "4k3/8/4p3/3pP3/3P4/8/8/3BK3 w - - 0 1"),  # light bishop
    ("open_file", chess.STARTING_FEN),
    ("half_open_file", "4k3/pppppppp/8/8/8/8/PPPPPPPP/3RK3 w - - 0 1"),
    ("space", chess.STARTING_FEN),
]

SKEWER_STAYS = "2kr3r/p1p1b2p/2p1p3/P3Pp2/1P3R2/2P3P1/2bN3P/R1B3K1 b - - 0 23"

# (concept, FEN, move, square the fact names)
MOVE_POSITIVE = [
    ("fork", "r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "b5c7", "a8"),
    ("skewer", "8/6r1/8/4k3/8/8/8/2B1K3 w - - 0 1", "c1b2", "g7"),
    # The check Bb7+ only delays the fork (#487).
    ("fork", "r1b1r3/6k1/8/1N6/8/P3P3/7P/7K w - - 0 1", "b5c7", "e8"),
    # The check Ng3+ only delays the skewer (#487).
    ("skewer", "8/k4n2/8/3r3n/8/3P3P/2B2PP1/7K w - - 0 1", "c2b3", "f7"),
    # The knight uncovers the rook: a discovered skewer (#487).
    ("skewer", "4r3/8/8/4k3/8/8/4N3/4R1K1 w - - 0 1", "e2c1", "e8"),
    # The front rook may stay: then it is the piece taken (#497).
    ("skewer", SKEWER_STAYS, "e7g5", "d2"),
    # Bb7+ Kg1 Nf3+: two checks in a row only delay the fork (#498).
    ("fork", "r1b1r3/6k1/8/1N6/7n/P3P3/7P/7K w - - 0 1", "b5c7", "e8"),
    ("discovered_attack", "4q1k1/8/8/8/8/8/4N3/4R1K1 w - - 0 1", "e2c3", "e8"),
    ("discovered_check", "4k3/8/8/8/8/8/4N3/4R1K1 w - - 0 1", "e2c3", "e8"),
]

MOVE_NEGATIVE = [
    ("fork", "r2qk3/8/8/1N6/8/8/8/4K3 w - - 0 1", "b5c7"),  # Qxc7
    ("skewer", "7k/6q1/8/4n3/8/8/8/2B1K3 w - - 0 1", "c1b2"),  # a pin, not a skewer
    ("fork", "r1b1r3/6k1/8/1N6/8/P7/7P/7K w - - 0 1", "b5c7"),  # Re1+ saves the rook
    ("skewer", "8/k4n2/8/3r3n/8/7P/2B2PP1/7K w - - 0 1", "c2b3"),  # Rd1+ saves the rook
    ("skewer", "4r3/8/8/4kb2/8/8/4N3/4R1K1 w - - 0 1", "e2c1"),  # Be4 blocks the rook
    # Rxc2 wins at least as much as the skewer (#497).
    ("skewer", "2kr3r/p1p1b2p/4p3/P1p1Pp2/1P3R2/2P3P1/R1bN3P/2B3K1 b - - 1 24", "e7g5"),
    ("skewer", "2r3k1/p2q1pbp/1p2pnp1/nB1p4/3P4/1Pr1P2P/P1QB1PP1/2R2RK1 w - - 0 1", "d2e1"),
    # Mate: the king in front never moves (#497).
    ("skewer", "1k1r4/ppp2Q2/3p4/3P4/4P3/P7/2q2Pr1/3RK3 b - - 0 28", "g2g1"),
    # A third check in a row breaks the fork (#498).
    ("fork", "r1b1r3/6k1/8/1N6/8/P3P1Nr/7P/7K w - - 0 1", "b5c7"),
    ("discovered_attack", "4q1k1/8/8/8/8/8/4N3/4R1K1 w - - 0 1", "g1h1"),
    ("discovered_check", "4k3/8/8/8/8/8/4N3/4R1K1 w - - 0 1", "g1h1"),
]

GREEK_GIFT = "r1bq1rk1/pppn1ppp/4p3/3pP3/3P4/2PB1N2/PP3PPP/RNBQK2R w KQ - 0 1"
GREEK_MOVE = chess.Move.from_uci("d3h7")
GREEK_LINE = ["g8h7", "f3g5", "h7g8", "d1h5"]  # Bxh7+ Kxh7 Ng5+ Kg8 Qh5
QUIET_SAC = "4k3/8/8/3p4/8/8/2B5/4K3 w - - 0 1"  # Be4 dxe4 gives a bishop for nothing
QUIET_MOVE = chess.Move.from_uci("c2e4")
DEFEND = "rnbqkb1r/pppppppp/5n2/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 1 2"


def _names(facts: list[ConceptFact], concept: str) -> list[ConceptFact]:
    return [f for f in facts if f.concept == concept]


@pytest.mark.parametrize(("concept", "fen", "square"), POSITION_POSITIVE)
def test_position_concept_found(concept: str, fen: str, square: str) -> None:
    board = chess.Board(fen)
    assert board.is_valid()
    found = _names(position_concepts(board), concept)
    assert any(chess.parse_square(square) in f.squares for f in found), found
    assert all(square in f.text for f in found if chess.parse_square(square) in f.squares)


@pytest.mark.parametrize(("concept", "fen"), POSITION_NEGATIVE)
def test_position_concept_absent(concept: str, fen: str) -> None:
    board = chess.Board(fen)
    assert board.is_valid()
    assert _names(position_concepts(board), concept) == []


@pytest.mark.parametrize(("concept", "fen", "uci", "square"), MOVE_POSITIVE)
def test_move_concept_found(concept: str, fen: str, uci: str, square: str) -> None:
    board = chess.Board(fen)
    assert board.is_valid()
    found = _names(move_concepts(board, chess.Move.from_uci(uci)), concept)
    assert len(found) == 1
    assert chess.parse_square(square) in found[0].squares
    assert square in found[0].text


@pytest.mark.parametrize(("concept", "fen", "uci"), MOVE_NEGATIVE)
def test_move_concept_absent(concept: str, fen: str, uci: str) -> None:
    board = chess.Board(fen)
    assert board.is_valid()
    assert _names(move_concepts(board, chess.Move.from_uci(uci)), concept) == []


def test_fact_texts() -> None:
    board = chess.Board(POSITION_POSITIVE[3][1])
    assert _names(position_concepts(board), "overloading")[0].text == (
        "the black queen on d7 is overloaded: it alone guards the black knight on b5 and "
        "the black bishop on h3, and after Rxb5 Qxb5 the black bishop on h3 is left unguarded."
    )
    fork = move_concepts(chess.Board(MOVE_POSITIVE[0][1]), chess.Move.from_uci("b5c7"))
    assert fork[0].text == (
        "Nc7+ is a fork: the white knight on c7 attacks the black rook on a8 and the black "
        "king on e8, and one of them is lost."
    )
    opened = _names(
        move_concepts(chess.Board(MOVE_POSITIVE[4][1]), chess.Move.from_uci("e2c1")), "skewer"
    )
    assert opened[0].text == (
        "Nc1+ opens a skewer: the white rook on e1 attacks the black king on e5, and when the "
        "king moves, the white rook on e1 takes the black rook on e8."
    )
    stays = _names(move_concepts(chess.Board(SKEWER_STAYS), chess.Move.from_uci("e7g5")), "skewer")
    assert stays[0].text == (
        "Bg5 skewers the white rook on f4: if the rook moves, the black bishop on g5 takes the "
        "white knight on d2; if it stays, the bishop takes the rook."
    )
    blockade_fen = next(fen for concept, fen, _ in POSITION_POSITIVE if concept == "blockade")
    blockade = _names(position_concepts(chess.Board(blockade_fen)), "blockade")
    assert blockade[0].text == "the black knight on d6 blockades the passed pawn on d5."


def test_static_exchange() -> None:
    board = chess.Board("4k3/8/4p3/3n4/8/8/8/3RK3 w - - 0 1")
    assert static_exchange(board, chess.Move.from_uci("d1d5")) == 3 - 5
    board = chess.Board("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1")
    assert static_exchange(board, chess.Move.from_uci("d1d5")) == 3


def test_sacrifice_found() -> None:
    board = chess.Board(GREEK_GIFT)
    fact = sacrifice(board, GREEK_MOVE, GREEK_LINE, move_score_cp=150, best_score_cp=170)
    assert fact is not None
    assert fact.text == (
        "Bxh7+ is a sacrifice: after the engine's line White has given up a bishop for a "
        "pawn, and the move is still close to the best."
    )


def test_sacrifice_absent_when_score_drops() -> None:
    board = chess.Board(GREEK_GIFT)
    assert sacrifice(board, GREEK_MOVE, GREEK_LINE, move_score_cp=90, best_score_cp=170) is None


def test_sacrifice_absent_without_material() -> None:
    board = chess.Board(GREEK_GIFT)
    quiet = chess.Move.from_uci("h2h3")
    assert sacrifice(board, quiet, ["a7a6"], move_score_cp=0, best_score_cp=0) is None


def test_compensation_names_the_gain() -> None:
    facts = compensation(chess.Board(GREEK_GIFT), GREEK_MOVE, GREEK_LINE)
    assert [f.text for f in facts] == [
        "In return White attacks the black king, giving check with Bxh7+ and Ng5+."
    ]


def test_compensation_absent_for_a_plain_loss() -> None:
    board = chess.Board(QUIET_SAC)
    assert sacrifice(board, QUIET_MOVE, ["d5e4"], move_score_cp=0, best_score_cp=0) is not None
    assert compensation(board, QUIET_MOVE, ["d5e4"]) == []


def test_compensation_only_after_a_sacrifice() -> None:
    board = chess.Board(GREEK_GIFT)
    facts = intent_concepts(board, GREEK_MOVE, GREEK_LINE, move_score_cp=0, best_score_cp=170)
    assert [f.concept for f in facts] == ["initiative"]


def test_initiative_found() -> None:
    fact = initiative(chess.Board(GREEK_GIFT), GREEK_MOVE, GREEK_LINE)
    assert fact is not None
    assert fact.text == (
        "White has the initiative: Bxh7+, Ng5+ and Qh5 each force Black to answer."
    )


def test_initiative_absent_for_quiet_moves() -> None:
    board = chess.Board(GREEK_GIFT)
    assert initiative(board, chess.Move.from_uci("h2h3"), ["a7a6", "a2a3", "b7b6"]) is None


def test_prophylaxis_found() -> None:
    fact = prophylaxis(chess.Board(DEFEND), chess.Move.from_uci("d2d3"), ["f6e4"], ["e7e5"])
    assert fact is not None
    assert fact.text == (
        "d3 is prophylaxis: it stops Black's Nxe4, which would have won the white pawn on e4."
    )


def test_prophylaxis_absent_when_the_threat_still_works() -> None:
    move = chess.Move.from_uci("h2h3")
    assert prophylaxis(chess.Board(DEFEND), move, ["f6e4"], ["f6e4"]) is None


def test_prophylaxis_absent_for_a_threatening_move() -> None:
    # e5 also defends nothing new, and it attacks the knight on f6: a threat, not a quiet move.
    board = chess.Board(DEFEND)
    assert prophylaxis(board, chess.Move.from_uci("e4e5"), ["f6e4"], ["f6d5"]) is None


def test_prophylaxis_absent_when_the_attacked_piece_moves_away() -> None:
    board = chess.Board("1r4k1/1Q6/8/8/8/8/8/6K1 w - - 0 1")
    assert prophylaxis(board, chess.Move.from_uci("b7a6"), ["b8b7"], ["g8g7"]) is None


def test_intent_concepts_all_three() -> None:
    board = chess.Board(GREEK_GIFT)
    facts = intent_concepts(board, GREEK_MOVE, GREEK_LINE, move_score_cp=150, best_score_cp=170)
    assert [f.concept for f in facts] == ["sacrifice", "compensation", "initiative"]


def test_illegal_move_is_refused() -> None:
    with pytest.raises(ValueError):
        move_concepts(chess.Board(), chess.Move.from_uci("e2e5"))


_NUMBER = re.compile(r"(?<![a-h])\d")


def test_text_has_no_numbers_or_bare_compensation() -> None:
    texts = [f.text for _, fen, _ in POSITION_POSITIVE for f in position_concepts(chess.Board(fen))]
    for _, fen, uci, _ in MOVE_POSITIVE:
        texts += [f.text for f in move_concepts(chess.Board(fen), chess.Move.from_uci(uci))]
    board = chess.Board(GREEK_GIFT)
    texts += [
        f.text
        for f in intent_concepts(
            board, GREEK_MOVE, GREEK_LINE, move_score_cp=150, best_score_cp=170
        )
    ]
    assert texts
    for text in texts:
        assert not _NUMBER.search(text), text
        assert "compensation" not in text.lower(), text
