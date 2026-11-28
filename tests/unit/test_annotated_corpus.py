"""Descriptive notation, book reading and comment alignment for the annotated corpus (#463)."""

from __future__ import annotations

import re
from pathlib import Path

import chess
import pytest

from scan64.learning.annotated_corpus.books import BookLayout, read_games
from scan64.learning.annotated_corpus.corpus import Game, Rejection, replay, report
from scan64.learning.annotated_corpus.descriptive import DescriptiveError, Origins, parse_move
from scan64.learning.annotated_corpus.sentences import split_sentences

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "annotated_corpus"
    / "chess_strategy_game1.txt"
)
CHESS_STRATEGY = BookLayout(
    header=re.compile(r"^\s+GAME No\. (?P<number>\d+)\.?\s*$"), separator=None, stop_at_result=True
)


def _play(texts: list[str]) -> tuple[chess.Board, Origins, list[str]]:
    board = chess.Board()
    origins = Origins(board)
    sans = []
    for text in texts:
        move = parse_move(board, text, origins.squares).move
        sans.append(board.san(move))
        origins.push(board, move)
        board.push(move)
    return board, origins, sans


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        # Ranks count from the mover's side: Black's "P-K4" is e5.
        (["P-K4", "P-K4", "Kt-KB3", "Kt-QB3"], ["e4", "e5", "Nf3", "Nc6"]),
        # Spaced print and the × sign read the same as the compact form.
        (["P - Q 4", "P - Q 4", "P - Q B 4", "P × P"], ["d4", "d5", "c4", "dxc4"]),
        (["P-K4", "P-Q4", "PxP", "QxP"], ["e4", "d5", "exd5", "Qxd5"]),
        (
            ["P-K4", "P-K4", "Kt-KB3", "Kt-KB3", "B-B4", "B-B4", "Castles", "Castles"],
            ["e4", "e5", "Nf3", "Nf6", "Bc4", "Bc5", "O-O", "O-O"],
        ),
    ],
)
def test_moves_convert_to_san(texts: list[str], expected: list[str]) -> None:
    assert _play(texts)[2] == expected


def test_a_rook_is_named_by_the_side_it_started_on() -> None:
    # After castling the king's rook stands on f1 and both rooks reach e1.
    board, origins, _ = _play(
        [
            "P-K4",
            "P-K4",
            "Kt-KB3",
            "Kt-QB3",
            "B-B4",
            "B-B4",
            "Castles",
            "Kt-B3",
            "P-Q3",
            "P-Q3",
            "Kt-B3",
            "Castles",
            "B-K3",
            "B-K3",
            "Q-Q2",
            "Q-Q2",
        ]
    )
    assert board.san(parse_move(board, "KR-K1", origins.squares).move) == "Rfe1"
    assert board.san(parse_move(board, "QR-K1", origins.squares).move) == "Rae1"
    with pytest.raises(DescriptiveError, match="ambiguous between Rae1, Rfe1"):
        parse_move(board, "R-K1", origins.squares)


def test_two_fitting_moves_are_rejected_with_both_named() -> None:
    board, origins, _ = _play(["P-K4", "P-K4", "Kt-KB3", "Kt-QB3", "B-B4", "B-B4"])
    # Kt-Kt5 fits Ng5 only; P-Q3 fits d3 only; "P-Kt3" fits b3 and g3.
    with pytest.raises(DescriptiveError, match=r"ambiguous between b3, g3"):
        parse_move(board, "P-Kt3", origins.squares)


def test_no_fitting_move_is_rejected() -> None:
    with pytest.raises(DescriptiveError, match="no legal move fits"):
        parse_move(chess.Board(), "Q-R5", Origins(chess.Board()).squares)


def test_a_missing_check_mark_tells_a_quiet_move_from_a_check() -> None:
    # 1.e4 e6 2.d4 d5 3.Nc3 Nf6: "B-Kt5" fits Bb5+ and Bg5; without "ch" it is Bg5.
    board, origins, _ = _play(["P-K4", "P-K3", "P-Q4", "P-Q4", "Kt-QB3", "Kt-KB3"])
    parsed = parse_move(board, "B-Kt5", origins.squares)
    assert board.san(parsed.move) == "Bg5"
    assert parsed.by_missing_check
    assert board.san(parse_move(board, "B-Kt5ch", origins.squares).move) == "Bb5+"


def test_en_passant_and_promotion() -> None:
    board = chess.Board()
    origins = Origins(board)
    for uci in ["e2e4", "a7a6", "e4e5", "d7d5"]:
        origins.push(board, chess.Move.from_uci(uci))
        board.push_uci(uci)
    assert board.san(parse_move(board, "PxP e.p.", origins.squares).move) == "exd6"
    promotion = chess.Board("8/P6k/8/8/8/8/7K/8 w - - 0 1")
    origins_at = {square: None for square in chess.SQUARES if promotion.piece_at(square)}
    assert promotion.san(parse_move(promotion, "P-R8=Q", origins_at).move) == "a8=Q"
    with pytest.raises(DescriptiveError):
        parse_move(promotion, "P-R8", origins_at)


def test_sentences_rebuild_the_comment() -> None:
    comment = "Not QxR, because of Q-B7ch. After 8. KtxP Dr. Lasker played B-Kt5! Why? It wins."
    sentences = split_sentences(comment)
    assert sentences == [
        "Not QxR, because of Q-B7ch.",
        "After 8. KtxP Dr. Lasker played B-Kt5!",
        "Why?",
        "It wins.",
    ]
    assert " ".join(sentences) == comment


def test_a_move_placeholder_does_not_end_a_sentence() -> None:
    comment = (
        "Black loses a tempo by playing 6. ... Kt-B3 at once. Better was KtxKt; 7. ... KtPxKt."
    )
    assert split_sentences(comment) == [
        "Black loses a tempo by playing 6. ... Kt-B3 at once.",
        "Better was KtxKt; 7. ... KtPxKt.",
    ]


def test_the_fixture_game_replays_and_each_comment_sits_after_its_move() -> None:
    games = read_games(FIXTURE.read_text(encoding="utf-8"), CHESS_STRATEGY)
    assert len(games) == 1
    game = replay("lasker_chess_strategy", games[0])
    assert isinstance(game, Game)
    assert (game.white, game.black, game.result) == ("Tartakower", "Burn", "Resigns")
    assert game.sans[:7] == ("e4", "e5", "f4", "Bc5", "Nf3", "d6", "fxe5")
    # The first comment follows 4.PxP: the FEN is the position after fxe5.
    first = next(comment for comment in game.comments if comment.ply > 0)
    assert (first.ply, first.after_san) == (7, "fxe5")
    board = chess.Board()
    for san in game.sans[:7]:
        board.push_san(san)
    assert first.fen == board.fen()
    assert first.sentences[0].startswith("On principle this exchange cannot be commended")
    # Every move is legal from the start and every comment's FEN is on the game's line.
    board = chess.Board()
    fens = [board.fen()]
    for san in game.sans:
        board.push_san(san)
        fens.append(board.fen())
    assert board.fen() == game.final_fen
    for comment in game.comments:
        assert comment.fen == fens[comment.ply]
        assert " ".join(comment.sentences) == comment.text
    assert report([game]).rejected == {}


def test_a_game_that_does_not_convert_is_rejected_whole() -> None:
    text = FIXTURE.read_text(encoding="utf-8").replace("2. P-KB4", "2. P-KR6", 1)
    result = replay("lasker_chess_strategy", read_games(text, CHESS_STRATEGY)[0])
    assert isinstance(result, Rejection)
    assert result.kind == "no legal move fits"
    assert "ply 3" in result.reason
