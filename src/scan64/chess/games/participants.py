"""Naming for the two sides of a game created from a play session."""

from __future__ import annotations

from collections.abc import Sequence

import chess

from scan64.chess.games.models import Game


def player_color(initial_fen: str | None, moves: Sequence[str] = ()) -> chess.Color:
    """The colour the human plays: whoever is to move once ``moves`` are replayed.

    Between requests it is always the human's turn — a move request applies the
    human's move and the opponent's reply together — so the side to move after
    replaying the stored moves is the human's side.
    """
    board = chess.Board(initial_fen) if initial_fen else chess.Board()
    for uci in moves:
        board.push_uci(uci)
    return board.turn


def opponent_name(opponent_config: dict[str, str]) -> str:
    provider = opponent_config.get("provider", "stockfish")
    if provider == "maia":
        checkpoint = opponent_config.get("maia_checkpoint")
        return f"Maia {checkpoint}" if checkpoint else "Maia"
    strength = opponent_config.get("strength")
    return f"Stockfish (strength {strength})" if strength else "Stockfish"


def participants(
    player_id: str,
    opponent_config: dict[str, str],
    initial_fen: str | None = None,
) -> tuple[str, str]:
    """Return the ``(white, black)`` names for a game played by ``player_id``."""
    opponent = opponent_name(opponent_config)
    if player_color(initial_fen) == chess.WHITE:
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
        colour = player_color(game.headers.get("FEN"), game.moves)
    except ValueError as error:
        raise CorruptGameError(f"Stored game {game.id} cannot be replayed") from error
    if colour == chess.WHITE:
        game.white, game.black = player_id, opponent
    else:
        game.white, game.black = opponent, player_id
    return True
