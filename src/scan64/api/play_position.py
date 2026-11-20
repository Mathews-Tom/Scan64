"""Resolve the authoritative owned Play position before generating engine help."""

from __future__ import annotations

from uuid import UUID

import chess
from fastapi import HTTPException
from sqlmodel import Session

from scan64.chess.boards import board_from
from scan64.chess.games.assistance import PlaySnapshot
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import is_expired, utc_now
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.participants import player_color


def owned_play_position(
    session: Session, session_id: UUID, player_id: str
) -> tuple[chess.Board, PlaySnapshot]:
    play = session.get(PlaySession, session_id)
    if play is None or play.player_id != player_id:
        raise HTTPException(status_code=404, detail="PlaySession not found")
    game = session.get(Game, play.game_id) if play.game_id else None
    if game is not None and game.owner_player_id not in (None, player_id):
        raise HTTPException(status_code=404, detail="PlaySession not found")
    if play.status != "active" or (game is not None and game.result != "*"):
        raise HTTPException(status_code=409, detail="PlaySession is not active")
    if play.game_id is not None and game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    clock = session.get(PlayClock, session_id)
    if clock is not None and is_expired(clock, utc_now()):
        raise HTTPException(status_code=409, detail="PlaySession clock has expired")
    # Standard sessions gain a Game on their first move; custom starts already have one.
    snapshot = PlaySnapshot.capture(play, game)
    try:
        board = board_from(snapshot.initial_fen)
        for uci in snapshot.moves:
            move = chess.Move.from_uci(uci)
            if not board.is_legal(move):
                raise ValueError("Illegal stored move")
            board.push(move)
    except ValueError:
        raise HTTPException(status_code=409, detail="Stored game has illegal moves") from None
    colour = player_color(
        snapshot.initial_fen,
        snapshot.moves,
        stored_color=snapshot.config.get("player_color")
        or (game.owner_color if game is not None else None),
    )
    if board.turn != colour:
        raise HTTPException(status_code=409, detail="Wait for the opponent's move")
    if not board.is_valid():
        raise HTTPException(status_code=422, detail="Invalid chess position")
    if board.is_game_over(claim_draw=False):
        raise HTTPException(status_code=409, detail="Position is game over")
    return board, snapshot
