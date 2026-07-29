import os
from pathlib import Path
from uuid import UUID

import chess
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session

from scan64.api.auth import require_authenticated_player
from scan64.chess.analysis.inflight import analysis_limiter
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.participants import name_player_on, participants
from scan64.chess.games.pgn import build_pgn
from scan64.chess.games.play_session_service import (
    PlaySessionNotActive,
    PlaySessionNotFound,
    PlaySessionService,
)
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.persistence.database import get_session
from scan64.providers.stockfish.adapter import StockfishConfig

router = APIRouter(tags=["play-sessions"])


class PlaySessionCreate(BaseModel):
    player_id: str
    game_id: UUID | None = None
    opponent_config: dict[str, str] = Field(default_factory=dict)
    clock_config: dict[str, str] | None = None
    initial_fen: str | None = None

    @field_validator("opponent_config")
    @classmethod
    def validate_opponent_config(cls, opponent_config: dict[str, str]) -> dict[str, str]:
        provider_name = opponent_config.get("provider", "stockfish")
        if provider_name not in {"stockfish", "maia"}:
            raise ValueError(f"Unsupported opponent provider: {provider_name}")
        return opponent_config

    @field_validator("initial_fen")
    @classmethod
    def validate_initial_fen(cls, initial_fen: str | None) -> str | None:
        if initial_fen is None:
            return None
        board = chess.Board(initial_fen)
        if not board.is_valid():
            raise ValueError("initial_fen must be a valid chess position")
        return initial_fen


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
    status: str


def get_opponent_provider(request: Request) -> StockfishOpponentProvider:
    pool_manager = getattr(request.app.state, "engine_pool_manager", None)
    return StockfishOpponentProvider(StockfishConfig(), pool_manager=pool_manager)


def get_maia_config_path() -> Path | None:
    raw_path = os.environ.get("SCAN64_MAIA_CONFIG")
    return Path(raw_path) if raw_path is not None else None


def get_play_session_service(
    session: Session = Depends(get_session),
    stockfish_provider: StockfishOpponentProvider = Depends(get_opponent_provider),
) -> PlaySessionService:
    return PlaySessionService(
        db_session=session,
        stockfish_provider=stockfish_provider,
        maia_config_path=get_maia_config_path(),
    )


def _get_owned_play_session(session_id: UUID, request: Request, session: Session) -> PlaySession:
    player_id = require_authenticated_player(request, session)
    play_session = session.get(PlaySession, session_id)
    if play_session is None or play_session.player_id != player_id:
        raise HTTPException(status_code=404, detail="PlaySession not found")
    return play_session


@router.post("/v1/play-sessions", response_model=PlaySessionRead)
def create_play_session(
    request: Request, session_in: PlaySessionCreate, session: Session = Depends(get_session)
) -> PlaySession:
    authenticated_player_id = require_authenticated_player(request, session)
    if authenticated_player_id != session_in.player_id:
        raise HTTPException(status_code=403, detail="Player bearer token does not match")
    game_id = session_in.game_id
    if session_in.initial_fen is not None:
        white, black = participants(
            session_in.player_id, session_in.opponent_config, session_in.initial_fen
        )
        game = Game(
            pgn="",
            headers={"FEN": session_in.initial_fen},
            moves=[],
            white=white,
            black=black,
            owner_player_id=session_in.player_id,
        )
        game.pgn = build_pgn(game)
        session.add(game)
        session.flush()
        game_id = game.id
    elif game_id is not None:
        existing = session.get(Game, game_id)
        if existing is None or existing.owner_player_id != authenticated_player_id:
            raise HTTPException(status_code=404, detail="Game not found")
        if name_player_on(existing, session_in.player_id, session_in.opponent_config):
            session.add(existing)

    play_session = PlaySession(
        player_id=session_in.player_id,
        game_id=game_id,
        opponent_config=session_in.opponent_config,
        clock_config=session_in.clock_config,
    )
    session.add(play_session)
    session.commit()
    session.refresh(play_session)
    return play_session


@router.get("/v1/play-sessions/{session_id}", response_model=PlaySessionRead)
def get_play_session(
    session_id: UUID, request: Request, session: Session = Depends(get_session)
) -> PlaySession:
    return _get_owned_play_session(session_id, request, session)


def schedule_pending_analysis(
    service: PlaySessionService, background_tasks: BackgroundTasks
) -> None:
    for player_id, job_id in service.pending_analysis:
        background_tasks.add_task(analysis_limiter.submit, player_id, job_id)
    service.pending_analysis.clear()


@router.post("/v1/play-sessions/{session_id}/moves", response_model=PlayMoveResponse)
async def create_move(
    request: Request,
    session_id: UUID,
    move_in: PlayMoveCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    service: PlaySessionService = Depends(get_play_session_service),
) -> PlayMoveResponse:
    play_session = _get_owned_play_session(session_id, request, session)
    try:
        opponent_move = await service.make_move(session_id, move_in.move)
        schedule_pending_analysis(service, background_tasks)
        return PlayMoveResponse(opponent_move=opponent_move, status=play_session.status)
    except PlaySessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PlaySessionNotActive as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/play-sessions/{session_id}/resign", response_model=PlaySessionRead)
def resign_play_session(
    session_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    service: PlaySessionService = Depends(get_play_session_service),
) -> PlaySession:
    play_session = _get_owned_play_session(session_id, request, session)
    try:
        play_session = service.resign(session_id)
    except PlaySessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PlaySessionNotActive as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    schedule_pending_analysis(service, background_tasks)
    return play_session
