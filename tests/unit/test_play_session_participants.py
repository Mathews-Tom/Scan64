from __future__ import annotations

import chess
import pytest

from scan64.chess.games.models import Game
from scan64.chess.games.participants import (
    CorruptGameError,
    name_player_on,
    participants,
    player_color,
)

BLACK_TO_MOVE_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def test_player_takes_white_from_the_standard_start() -> None:
    assert participants("alice", {"provider": "stockfish", "strength": "10"}) == (
        "alice",
        "Stockfish (strength 10)",
    )


def test_player_takes_the_side_to_move_of_a_seeded_position() -> None:
    assert participants("alice", {"strength": "6"}, BLACK_TO_MOVE_FEN) == (
        "Stockfish (strength 6)",
        "alice",
    )


def test_maia_opponent_is_named_by_its_selected_checkpoint() -> None:
    white, black = participants("alice", {"provider": "maia", "maia_checkpoint": "1500"})
    assert (white, black) == ("alice", "Maia 1500")


def test_opponent_without_a_strength_setting_is_named_by_provider_alone() -> None:
    assert participants("alice", {})[1] == "Stockfish"


def test_player_colour_defaults_to_white_from_the_standard_start() -> None:
    assert player_color(None) == chess.WHITE


def test_player_colour_follows_the_side_to_move_of_a_seeded_position() -> None:
    assert player_color(BLACK_TO_MOVE_FEN) == chess.BLACK


def test_player_colour_is_taken_after_replaying_the_played_moves() -> None:
    assert player_color(None, ["e2e4"]) == chess.BLACK
    assert player_color(None, ["e2e4", "e7e5"]) == chess.WHITE
    assert player_color(BLACK_TO_MOVE_FEN, ["e7e5"]) == chess.WHITE


def test_a_resumed_game_names_its_owner_on_the_side_they_play() -> None:
    game = Game(pgn="", moves=["e2e4"], white="Unknown", black="Unknown", owner_player_id="alice")

    name_player_on(game, "alice", {"strength": "8"})

    assert (game.white, game.black) == ("Stockfish (strength 8)", "alice")


def test_another_players_game_keeps_its_imported_names() -> None:
    game = Game(pgn="", moves=[], white="Kasparov", black="Karpov", owner_player_id="bob")

    name_player_on(game, "alice", {"strength": "8"})

    assert (game.white, game.black) == ("Kasparov", "Karpov")


def test_naming_reports_whether_it_renamed_the_game() -> None:
    owned = Game(pgn="", moves=[], white="Unknown", black="Unknown", owner_player_id="alice")
    foreign = Game(pgn="", moves=[], white="Kasparov", black="Karpov", owner_player_id="bob")

    assert name_player_on(owned, "alice", {}) is True
    assert name_player_on(foreign, "alice", {}) is False


def test_naming_a_game_whose_moves_do_not_fit_it_is_loud() -> None:
    corrupt = Game(pgn="", moves=["e2e4", "e2e4"], white="a", black="b", owner_player_id="alice")

    with pytest.raises(CorruptGameError, match="cannot be replayed"):
        name_player_on(corrupt, "alice", {})
