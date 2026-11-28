"""Variant rules come from python-chess boards; legality, FEN and SAN per variant."""

from __future__ import annotations

import io

import chess
import chess.pgn
import chess.variant
import pytest

from scan64.chess.games.ingestion import NonStandardVariantError, ingest_pgn
from scan64.chess.variants import (
    STANDARD,
    SUPPORTED_VARIANTS,
    UnsupportedVariantError,
    board_class,
    board_variant,
    display_name,
    stored_variant,
    variant_from_tag,
)

# One short legal game per variant, each ending on a move that only that
# variant's rules allow or decide.
GAMES: dict[str, tuple[str, str, str]] = {
    # (PGN Variant tag, movetext, SAN of the last move)
    "crazyhouse": ("Crazyhouse", "1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 4. P@e5", "@e5"),
    "3check": ("Three-check", "1. e4 e5 2. Bc4 Nc6 3. Bxf7+", "Bxf7+"),
    "atomic": ("Atomic", "1. Nf3 f6 2. Ne5 fxe5", "fxe5"),
    "antichess": ("Antichess", "1. e3 b5 2. Bxb5", "Bxb5"),
    "kingofthehill": ("King of the Hill", "1. e4 e5 2. Ke2 Ke7 3. Ke3 Ke6 4. Kf3", "Kf3"),
    "horde": ("Horde", "1. e5 d6 2. exd6", "exd6"),
    "racingkings": ("Racing Kings", "1. Kh3 Kb3", "Kb3"),
}


def test_every_supported_variant_has_a_python_chess_board() -> None:
    assert SUPPORTED_VARIANTS[0] == STANDARD
    for name in SUPPORTED_VARIANTS:
        assert board_class(name).uci_variant == name
    assert set(GAMES) == set(SUPPORTED_VARIANTS) - {STANDARD}


@pytest.mark.parametrize("tag", [None, "Standard", "From Position", "chess"])
def test_standard_tags(tag: str | None) -> None:
    assert variant_from_tag(tag) == STANDARD


@pytest.mark.parametrize("tag", ["Chess960", "Suicide", "Giveaway", "Bughouse", "nonsense"])
def test_unsupported_tags_are_refused_naming_the_supported_set(tag: str) -> None:
    with pytest.raises(UnsupportedVariantError, match=f"Variant '{tag}' is not supported"):
        variant_from_tag(tag)


@pytest.mark.parametrize("variant", sorted(GAMES))
def test_variant_games_replay_with_correct_legality_fen_and_san(variant: str) -> None:
    tag, movetext, last_san = GAMES[variant]
    game = chess.pgn.read_game(io.StringIO(f'[Variant "{tag}"]\n\n{movetext} *\n'))
    assert game is not None
    assert game.errors == []
    assert variant_from_tag(tag) == variant
    assert board_variant(game.board()) == variant
    board = game.board()
    moves = list(game.mainline_moves())
    for move in moves[:-1]:
        board.push(move)
    assert board.san(moves[-1]) == last_san
    board.push(moves[-1])
    # The FEN round-trips through the same variant board.
    assert board_class(variant)(board.fen()).fen() == board.fen()


def test_crazyhouse_fen_carries_the_pockets() -> None:
    board = chess.variant.CrazyhouseBoard()
    for san in ("e4", "d5", "exd5", "Qxd5"):
        board.push_san(san)
    assert board.fen().split()[0].endswith("[Pp]")


def test_illegal_variant_move_is_a_pgn_error() -> None:
    game = chess.pgn.read_game(io.StringIO('[Variant "Crazyhouse"]\n\n1. e4 d5 2. N@e5 *\n'))
    assert game is not None
    assert game.errors


def test_board_variant_refuses_chess960() -> None:
    with pytest.raises(UnsupportedVariantError, match="Chess960"):
        board_variant(chess.Board.from_chess960_pos(100))


def test_stored_variant_keeps_a_legacy_unsupported_tag() -> None:
    assert stored_variant({"Variant": "Three-check"}) == "3check"
    assert stored_variant({"Variant": "Chess960"}) == "Chess960"
    assert stored_variant({}) == STANDARD
    assert display_name("kingofthehill") == "King of the Hill"


def test_standard_ingestion_labels_a_variant_game_as_not_covered() -> None:
    with pytest.raises(NonStandardVariantError, match="Three-check games are not covered"):
        ingest_pgn('[Variant "Three-check"]\n\n1. e4 e5 *\n')
    with pytest.raises(NonStandardVariantError, match="Variant 'Chess960' is not supported"):
        ingest_pgn('[Variant "Chess960"]\n\n1. e4 e5 *\n')
    game, positions = ingest_pgn('[Variant "From Position"]\n\n1. e4 e5 *\n')
    assert len(positions) == 3
