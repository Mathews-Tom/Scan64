"""On-demand hints use the reserved interactive capacity, never batch engines."""

from typing import Self
from uuid import UUID

import chess
import chess.engine
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator
from sqlmodel import Session

from scan64.api.auth import require_authenticated_player
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game, PlaySession
from scan64.learning.insights import PositionInsight, position_insight
from scan64.persistence.database import get_session
from scan64.providers.stockfish.pool import EnginePoolManager

router = APIRouter(tags=["insights"])


class PositionInsightRequest(BaseModel):
    player_id: str
    play_session_id: UUID | None = None
    fen: str | None = None

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if (self.play_session_id is None) == (self.fen is None):
            raise ValueError("Supply exactly one of play_session_id or fen")
        return self


def _position(body: PositionInsightRequest, session: Session) -> chess.Board:
    if body.play_session_id is not None:
        play = session.get(PlaySession, body.play_session_id)
        if play is None or play.player_id != body.player_id:
            raise HTTPException(status_code=404, detail="PlaySession not found")
        game = session.get(Game, play.game_id) if play.game_id else None
        if play.status != "active" or (game is not None and game.result != "*"):
            raise HTTPException(status_code=409, detail="PlaySession is not active")
        if game is None:
            raise HTTPException(status_code=404, detail="Game not found")
        board = board_from(game.headers.get("FEN"))
        try:
            for uci in game.moves:
                board.push_uci(uci)
        except ValueError:
            raise HTTPException(status_code=409, detail="Stored game has illegal moves") from None
        if game.owner_color is not None and board.turn != (game.owner_color == "white"):
            raise HTTPException(status_code=409, detail="Wait for the opponent's move")
    else:
        try:
            board = chess.Board(body.fen)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid FEN") from None
    if not board.is_valid():
        raise HTTPException(status_code=422, detail="Invalid chess position")
    if board.is_game_over(claim_draw=True):
        raise HTTPException(status_code=409, detail="Position is game over")
    return board


@router.post("/v1/insights/position", response_model=PositionInsight)
async def insight_position(
    body: PositionInsightRequest, request: Request, session: Session = Depends(get_session)
) -> PositionInsight:
    player_id = require_authenticated_player(request, session)
    if player_id != body.player_id:
        raise HTTPException(status_code=404, detail="Player not found")
    board = _position(body, session)
    manager: EnginePoolManager | None = getattr(request.app.state, "engine_pool_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Hints require the interactive engine pool")

    if manager.interactive_pool.concurrency < 2:
        raise HTTPException(
            status_code=503, detail="Hints require at least two interactive engines"
        )
    try:
        analysis = await manager.analyze_hint(board.fen())
        return position_insight(board, analysis)
    except TimeoutError:
        raise HTTPException(
            status_code=503, detail="Hint capacity is busy; try again shortly"
        ) from None
    except (ValueError, chess.engine.EngineError, OSError):
        raise HTTPException(
            status_code=503, detail="The hint engine could not analyze this position"
        ) from None
