import os
import secrets
from pathlib import Path
from typing import Literal
from uuid import UUID

import chess
from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session

from scan64.api.auth import require_authenticated_player
from scan64.chess.analysis.jobs import submit_analysis_job
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.participants import name_player_on, participants, player_color
from scan64.chess.games.pgn import build_pgn
from scan64.chess.games.play_session_service import (
    PlaySessionNotActive,
    PlaySessionNotFound,
    PlaySessionService,
    TakebackNotAllowed,
)
from scan64.chess.opponents.bots import BOT_LEVELS, get_bot_level
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.persistence.database import get_session
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager

router = APIRouter(tags=["play-sessions"])


class PlaySessionCreate(BaseModel):
    player_id: str
    game_id: UUID | None = None
    opponent_config: dict[str, str] = Field(default_factory=dict, validate_default=True)
    clock_config: dict[str, str] | None = None
    initial_fen: str | None = None
    coach_mode: bool = False
    player_color: Literal["white", "black", "random"] | None = None

    @field_validator("opponent_config")
    @classmethod
    def validate_opponent_config(cls, opponent_config: dict[str, str]) -> dict[str, str]:
        provider_name = opponent_config.get("provider", "stockfish")
        if provider_name not in {"stockfish", "maia"}:
            raise ValueError(f"Unsupported opponent provider: {provider_name}")
        if provider_name == "stockfish":
            if "strength" in opponent_config:
                raise ValueError("Stockfish sessions use bot_level, not strength")
            level = opponent_config.get("bot_level", "1")
            if not level.isascii() or not level.isdecimal():
                raise ValueError("bot_level must identify one of the eight bot levels")
            get_bot_level(int(level))
            opponent_config["bot_level"] = str(int(level))
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
    coach_mode: bool
    player_color: Literal["white", "black"]
    opponent_move: str | None = None


class PlayMoveCreate(BaseModel):
    move: str


class CriticalInterruptionRead(BaseModel):
    lesson: LessonSpec
    opportunity_id: UUID
    study_session_id: str


class PlayMoveResponse(BaseModel):
    opponent_move: str | None
    status: str
    critical_interruption: CriticalInterruptionRead | None = None


def get_opponent_provider(request: Request) -> StockfishOpponentProvider:
    pool_manager = getattr(request.app.state, "engine_pool_manager", None)
    return StockfishOpponentProvider(StockfishConfig(), pool_manager=pool_manager)


def get_maia_config_path() -> Path | None:
    raw_path = os.environ.get("SCAN64_MAIA_CONFIG")
    return Path(raw_path) if raw_path is not None else None


def get_play_session_service(
    request: Request,
    session: Session = Depends(get_session),
    stockfish_provider: StockfishOpponentProvider = Depends(get_opponent_provider),
) -> PlaySessionService:
    return PlaySessionService(
        db_session=session,
        stockfish_provider=stockfish_provider,
        maia_config_path=get_maia_config_path(),
        engine_pool_manager=getattr(request.app.state, "engine_pool_manager", None),
    )


def _get_owned_play_session(session_id: UUID, request: Request, session: Session) -> PlaySession:
    player_id = require_authenticated_player(request, session)
    play_session = session.get(PlaySession, session_id)
    if play_session is None or play_session.player_id != player_id:
        raise HTTPException(status_code=404, detail="PlaySession not found")
    return play_session


def read_play_session(
    play_session: PlaySession, session: Session, opponent_move: str | None = None
) -> PlaySessionRead:
    game = session.get(Game, play_session.game_id) if play_session.game_id else None
    colour = player_color(
        game.headers.get("FEN") if game else None,
        game.moves if game else (),
        play_session.opponent_config.get("player_color") or (game.owner_color if game else None),
    )
    return PlaySessionRead(
        id=play_session.id,
        player_id=play_session.player_id,
        game_id=play_session.game_id,
        opponent_config=play_session.opponent_config,
        clock_config=play_session.clock_config,
        status=play_session.status,
        coach_mode=play_session.coach_mode,
        player_color="white" if colour else "black",
        opponent_move=opponent_move,
    )


class BotRead(BaseModel):
    level: int
    name: str
    description: str


@router.get("/v1/bots", response_model=list[BotRead], response_model_exclude_none=True)
def list_bots() -> list[BotRead]:
    return [
        BotRead(level=level.level, name=level.name, description=level.description)
        for level in BOT_LEVELS
    ]


@router.post("/v1/play-sessions", response_model=PlaySessionRead)
async def create_play_session(
    request: Request,
    session_in: PlaySessionCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    service: PlaySessionService = Depends(get_play_session_service),
) -> PlaySessionRead:
    authenticated_player_id = require_authenticated_player(request, session)
    if authenticated_player_id != session_in.player_id:
        raise HTTPException(status_code=403, detail="Player bearer token does not match")
    config = dict(session_in.opponent_config)
    # Client-supplied internal values cannot select another seed or undo boundary.
    config["seed"] = str(secrets.randbits(64))
    game = None
    if session_in.initial_fen is not None:
        board = chess.Board(session_in.initial_fen)
    elif session_in.game_id is not None:
        game = session.get(Game, session_in.game_id)
        if game is None or game.owner_player_id != authenticated_player_id:
            raise HTTPException(status_code=404, detail="Game not found")
        board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
        for move in game.moves:
            board.push_uci(move)
    else:
        board = chess.Board()
    requested_color = session_in.player_color
    if requested_color == "random":
        colour = bool(secrets.randbits(1))
    elif requested_color is None:
        colour = board.turn
    else:
        colour = requested_color == "white"
    config["player_color"] = "white" if colour else "black"
    if game is None and (session_in.initial_fen is not None or board.turn != colour):
        white, black = participants(session_in.player_id, config, session_in.initial_fen)
        game = Game(
            pgn="",
            headers={"FEN": session_in.initial_fen} if session_in.initial_fen else {},
            white=white,
            black=black,
            owner_player_id=session_in.player_id,
            owner_color="white" if colour else "black",
        )
        game.pgn = build_pgn(game)
    elif game is not None:
        game.owner_color = "white" if colour else "black"
        name_player_on(game, session_in.player_id, config)
    if game is not None:
        session.add(game)
        session.flush()
    play_session = PlaySession(
        player_id=session_in.player_id,
        game_id=game.id if game else None,
        opponent_config=config,
        clock_config=session_in.clock_config,
        coach_mode=session_in.coach_mode,
    )
    first_move = None
    if game is not None and board.turn != colour and not board.is_game_over():
        first_move = await service.play_opponent_move(play_session, game, board)
        game.pgn = build_pgn(game)
    play_session.opponent_config = {
        **play_session.opponent_config,
        "initial_ply": str(len(game.moves) if game else 0),
    }
    session.add(play_session)
    if board.is_game_over():
        session.flush()
        service.complete_session(play_session, game, board.result() if game else None)
        schedule_pending_analysis(
            service, background_tasks, getattr(request.app.state, "engine_pool_manager", None)
        )
    else:
        session.commit()
    session.refresh(play_session)
    return read_play_session(play_session, session, first_move)


@router.get("/v1/play-sessions/{session_id}", response_model=PlaySessionRead)
def get_play_session(
    session_id: UUID, request: Request, session: Session = Depends(get_session)
) -> PlaySessionRead:
    return read_play_session(_get_owned_play_session(session_id, request, session), session)


def schedule_pending_analysis(
    service: PlaySessionService,
    background_tasks: BackgroundTasks,
    pool_manager: EnginePoolManager | None,
) -> None:
    for player_id, job_id in service.pending_analysis:
        background_tasks.add_task(submit_analysis_job, player_id, job_id, pool_manager)
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
        move_result = await service.make_move(session_id, move_in.move)
        pool_manager = getattr(request.app.state, "engine_pool_manager", None)
        schedule_pending_analysis(service, background_tasks, pool_manager)
        interruption = move_result.interruption
        return PlayMoveResponse(
            opponent_move=move_result.opponent_move,
            status=play_session.status,
            critical_interruption=(
                CriticalInterruptionRead(
                    lesson=interruption.lesson,
                    opportunity_id=interruption.opportunity_id,
                    study_session_id=interruption.study_session_id,
                )
                if interruption is not None
                else None
            ),
        )
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
) -> PlaySessionRead:
    play_session = _get_owned_play_session(session_id, request, session)
    try:
        play_session = service.resign(session_id)
    except PlaySessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PlaySessionNotActive as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    pool_manager = getattr(request.app.state, "engine_pool_manager", None)
    schedule_pending_analysis(service, background_tasks, pool_manager)
    return read_play_session(play_session, session)


@router.post("/v1/play-sessions/{session_id}/takeback", response_model=PlaySessionRead)
def takeback_play_session(
    session_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
    service: PlaySessionService = Depends(get_play_session_service),
) -> PlaySessionRead:
    _get_owned_play_session(session_id, request, session)
    try:
        play_session = service.takeback(session_id)
    except (PlaySessionNotActive, TakebackNotAllowed) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return read_play_session(play_session, session)
