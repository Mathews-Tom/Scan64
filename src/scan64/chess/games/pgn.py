"""Serialisation of a stored game to PGN."""

from __future__ import annotations

import chess
import chess.pgn

from scan64.chess.games.models import Game
from scan64.chess.games.participants import CorruptGameError

__all__ = ["CorruptGameError", "build_pgn"]

EVENT_NAME = "Scan64 play session"


def build_pgn(game: Game) -> str:
    """Render ``game`` as a PGN that re-imports to the same moves and players.

    Tags the game already carries — ECO, Elo ratings, TimeControl and the rest
    — are kept; only the tags the stored row owns are overwritten. Comments,
    variations and annotations are not recoverable from the stored move list.
    """
    initial_fen = game.headers.get("FEN")
    board = chess.Board(initial_fen) if initial_fen else chess.Board()

    exported = chess.pgn.Game()
    exported.setup(board)
    for tag, value in game.headers.items():
        exported.headers[tag] = value
    # chess.pgn.Game() pre-seeds the seven-tag roster with "?", so these have
    # to consult the stored headers rather than default against the roster.
    exported.headers["Event"] = game.headers.get("Event", EVENT_NAME)
    exported.headers["Site"] = game.headers.get("Site", "Scan64")
    exported.headers["Round"] = game.headers.get("Round", "-")
    exported.headers["Date"] = game.date or game.created_at.strftime("%Y.%m.%d")
    exported.headers["White"] = game.white
    exported.headers["Black"] = game.black
    exported.headers["Result"] = game.result

    node: chess.pgn.GameNode = exported
    for uci in game.moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise CorruptGameError(f"Stored game {game.id} contains an illegal move: {uci}")
        board.push(move)
        node = node.add_main_variation(move)

    return str(exported)
