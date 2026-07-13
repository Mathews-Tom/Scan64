from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session

from scan64.chess.games.models import PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.persistence.database import get_session
from scan64.providers.stockfish.adapter import StockfishConfig

router = APIRouter(tags=["play-sessions"])


class PlaySessionCreate(BaseModel):
    player_id: str
    opponent_config: dict[str, str] = {}
    clock_config: dict[str, str] | None = None


class PlaySessionRead(BaseModel):
    id: UUID
    player_id: str
    game_id: UUID | None = None
    opponent_config: dict[str, str]
    clock_config: dict[str, str] | None
    status: str


class PlayMoveCreate(BaseModel):
    move: str


class PlayMoveResponse(BaseModel):
    opponent_move: str | None


def get_opponent_provider() -> StockfishOpponentProvider:
    # Provide the default stockfish opponent as required by
    # "existing M11 Stockfish opponent integration"
    return StockfishOpponentProvider(StockfishConfig())


def get_play_session_service(
    session: Session = Depends(get_session),
    opponent_provider: StockfishOpponentProvider = Depends(get_opponent_provider),
) -> PlaySessionService:
    return PlaySessionService(db_session=session, opponent_provider=opponent_provider)


@router.post("/v1/play-sessions", response_model=PlaySessionRead)
def create_play_session(
    session_in: PlaySessionCreate, session: Session = Depends(get_session)
) -> PlaySession:
    play_session = PlaySession(
        player_id=session_in.player_id,
        opponent_config=session_in.opponent_config,
        clock_config=session_in.clock_config,
    )
    session.add(play_session)
    session.commit()
    session.refresh(play_session)
    return play_session


@router.get("/v1/play-sessions/{session_id}", response_model=PlaySessionRead)
def get_play_session(
    session_id: UUID, session: Session = Depends(get_session)
) -> PlaySession:
    play_session = session.get(PlaySession, session_id)
    if not play_session:
        raise HTTPException(status_code=404, detail="PlaySession not found")
    return play_session


@router.post("/v1/play-sessions/{session_id}/moves", response_model=PlayMoveResponse)
async def create_move(
    request: Request,
    session_id: UUID,
    move_in: PlayMoveCreate,
    service: PlaySessionService = Depends(get_play_session_service),
) -> PlayMoveResponse:
    try:
        opponent_move = await service.make_move(session_id, move_in.move)
        return PlayMoveResponse(opponent_move=opponent_move)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

