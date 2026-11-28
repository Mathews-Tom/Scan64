"""Replay a book's games with python-chess and tie each comment to its position.

Every move text is converted from descriptive notation against the board
(``descriptive.parse_move``). A game converts only if every move does; one
that does not is rejected with the reason, never cut short. Each comment is
tied to the FEN after the move it follows and split into sentences.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import chess

from scan64.learning.annotated_corpus.books import BookGame
from scan64.learning.annotated_corpus.descriptive import DescriptiveError, Origins, parse_move
from scan64.learning.annotated_corpus.sentences import split_sentences


@dataclass(frozen=True)
class Comment:
    """A comment on the position after ``ply`` moves of a game."""

    ply: int
    # The move the comment follows, in SAN; ``None`` before the first move.
    after_san: str | None
    fen: str
    text: str
    sentences: tuple[str, ...]


@dataclass(frozen=True)
class Game:
    source: str
    number: str
    heading: str
    white: str | None
    black: str | None
    event: str | None
    result: str | None
    sans: tuple[str, ...]
    final_fen: str
    comments: tuple[Comment, ...]
    # Moves told apart from a checking move only because the text lacks "ch".
    by_missing_check: int


@dataclass(frozen=True)
class Rejection:
    source: str
    number: str
    heading: str
    # A short class for the report ("no legal move fits", "ambiguous", ...).
    kind: str
    reason: str


def _kind(reason: str) -> str:
    for kind in (
        "no legal move fits",
        "ambiguous",
        "not a descriptive move",
        "castling",
        "move number",
    ):
        if kind in reason:
            return kind
    return "layout"


def replay(source: str, book_game: BookGame) -> Game | Rejection:
    """The game with every move legal and every comment tied to a FEN, or why not."""

    def reject(reason: str) -> Rejection:
        return Rejection(source, book_game.number, book_game.heading, _kind(reason), reason)

    if book_game.problem is not None:
        return reject(book_game.problem)
    if not book_game.moves:
        return reject("layout: no moves")
    board = chess.Board()
    origins = Origins(board)
    fens = [board.fen()]
    sans: list[str] = []
    by_missing_check = 0
    for ply, text in enumerate(book_game.moves, start=1):
        try:
            parsed = parse_move(board, text, origins.squares)
        except DescriptiveError as error:
            side = "White" if board.turn == chess.WHITE else "Black"
            return reject(f"ply {ply} ({side}, move {board.fullmove_number}): {error}")
        move = parsed.move
        by_missing_check += parsed.by_missing_check
        sans.append(board.san(move))
        origins.push(board, move)
        board.push(move)
        fens.append(board.fen())
    comments = tuple(
        Comment(
            ply=ply,
            after_san=sans[ply - 1] if ply else None,
            fen=fens[ply],
            text=text,
            sentences=tuple(split_sentences(text)),
        )
        for ply, text in book_game.comments
    )
    return Game(
        source=source,
        number=book_game.number,
        heading=book_game.heading,
        white=book_game.white,
        black=book_game.black,
        event=book_game.event,
        result=book_game.result,
        sans=tuple(sans),
        final_fen=board.fen(),
        comments=comments,
        by_missing_check=by_missing_check,
    )


@dataclass(frozen=True)
class SourceReport:
    games_found: int
    games_replayed: int
    comments: int
    sentences: int
    moves: int
    by_missing_check: int
    rejected: dict[str, int]


def report(results: list[Game | Rejection]) -> SourceReport:
    games = [result for result in results if isinstance(result, Game)]
    rejected = Counter(result.kind for result in results if isinstance(result, Rejection))
    return SourceReport(
        games_found=len(results),
        games_replayed=len(games),
        comments=sum(len(game.comments) for game in games),
        sentences=sum(len(comment.sentences) for game in games for comment in game.comments),
        moves=sum(len(game.sans) for game in games),
        by_missing_check=sum(game.by_missing_check for game in games),
        rejected=dict(sorted(rejected.items())),
    )
