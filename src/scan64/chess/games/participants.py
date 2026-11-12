"""Naming for the two sides of a game created from a play session."""

from __future__ import annotations

from collections.abc import Sequence

import chess

from scan64.chess.games.models import Game
from scan64.chess.opponents.bots import get_bot_level


def player_color(
    initial_fen: str | None,
    moves: Sequence[str] = (),
    stored_color: str | None = None,
) -> chess.Color:
    """Use the durable human side; replay-based inference is for legacy rows only."""
    board = chess.Board(initial_fen) if initial_fen else chess.Board()
    for uci in moves:
        board.push_uci(uci)
    if stored_color in {"white", "black"}:
        return stored_color == "white"
    return board.turn


def opponent_name(opponent_config: dict[str, str]) -> str:
    provider = opponent_config.get("provider", "stockfish")
    if provider == "maia":
        checkpoint = opponent_config.get("maia_checkpoint")
        return f"Maia {checkpoint}" if checkpoint else "Maia"
    if "bot_level" in opponent_config:
        level = get_bot_level(int(opponent_config["bot_level"]))
        return f"Stockfish (level {level.level}: {level.name})"
    try:
        strength = int(opponent_config.get("strength", "0"))
    except ValueError:
        return "Stockfish"
    return f"Stockfish (skill level {max(0, min(20, strength))})"


def participants(
    player_id: str,
    opponent_config: dict[str, str],
    initial_fen: str | None = None,
) -> tuple[str, str]:
    """Return the ``(white, black)`` names for a game played by ``player_id``."""
    opponent = opponent_name(opponent_config)
    if player_color(initial_fen, stored_color=opponent_config.get("player_color")) == chess.WHITE:
        return player_id, opponent
    return opponent, player_id


class CorruptGameError(RuntimeError):
    """A stored game cannot be replayed because its own moves do not fit it."""


def name_player_on(game: Game, player_id: str, opponent_config: dict[str, str]) -> bool:
    """Name ``player_id`` on the side they are about to play in ``game``.

    Only a game the player already owns is renamed; another player's game and
    an ownerless legacy game keep the names they were imported with. Returns
    whether the game was renamed.
    """
    if game.owner_player_id != player_id:
        return False
    opponent = opponent_name(opponent_config)
    try:
        colour = player_color(
            game.headers.get("FEN"),
            game.moves,
            opponent_config.get("player_color") or game.owner_color,
        )
    except ValueError as error:
        raise CorruptGameError(f"Stored game {game.id} cannot be replayed") from error
    if colour == chess.WHITE:
        game.white, game.black = player_id, opponent
    else:
        game.white, game.black = opponent, player_id
    return True
