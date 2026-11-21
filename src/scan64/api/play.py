from __future__ import annotations

import asyncio
import os
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import chess
from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StrictInt, field_validator
from sqlmodel import Session, col, select

from scan64.api.auth import require_authenticated_player
from scan64.api.models import Player
from scan64.api.onboarding import manual_bot_level, onboarding_for
from scan64.chess.analysis.jobs import submit_analysis_job
from scan64.chess.games.assistance import AssistanceMode, StalePlaySnapshot, assistance_modes
from scan64.chess.games.clock_models import PlayClock, PlayClockRead, TimeControl
from scan64.chess.games.clocks import ClockConflict, PlayClockRuntime, project_clock, utc_now
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
from scan64.learning.sparring import (
    IneligibleSparringSource,
    SparringAttempt,
    SparringAttemptRead,
    SparringSource,
    SparringSourceNotFound,
    SparringSourceRequest,
    load_source,
    read_sparring,
)
from scan64.persistence.database import get_session
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager

router = APIRouter(tags=["play-sessions"])

BOT_POLICY_VERSION = "stockfish-error-budget-v1"
CLOCK_EXPIRY_WAIT_SECONDS = 15.0
CLOCK_CHANGE_WAIT_SECONDS = 5.0


class PlaySessionCreate(BaseModel):
    player_id: str
    game_id: UUID | None = None
    opponent_config: dict[str, str] = Field(default_factory=dict, validate_default=True)
    clock_config: TimeControl | None = None
    initial_fen: str | None = None
    coach_mode: bool = False
    player_color: Literal["white", "black", "random"] | None = None
    sparring_source: SparringSourceRequest | None = None

    @field_validator("opponent_config")
    @classmethod
    def validate_opponent_config(cls, opponent_config: dict[str, str]) -> dict[str, str]:
        provider_name = opponent_config.get("provider", "stockfish")
        if provider_name not in {"stockfish", "maia"}:
            raise ValueError(f"Unsupported opponent provider: {provider_name}")
        if provider_name == "stockfish":
            if "strength" in opponent_config:
                raise ValueError("Stockfish sessions use bot_level, not strength")
            level = opponent_config.get("bot_level")
            if level is not None:
                if not level.isascii() or not level.isdecimal():
                    raise ValueError("bot_level must identify one of the eight bot levels")
                get_bot_level(int(level))
                opponent_config["bot_level"] = str(int(level))
        elif "strength" in opponent_config:
            try:
                int(opponent_config["strength"])
            except ValueError as error:
                raise ValueError("Maia strength must be an integer rating") from error
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
    sparring: SparringAttemptRead | None = None
    assistance_modes: list[AssistanceMode]
    clock: PlayClockRead | None = None
    pgn: str | None = None
    result: str | None = None


class PlayMoveCreate(BaseModel):
    move: str
    expected_generation: StrictInt | None = Field(default=None, ge=0)
    expected_clock_version: StrictInt | None = None


class OpponentMoveCreate(BaseModel):
    expected_clock_version: StrictInt


class PlayResumeCreate(BaseModel):
    expected_clock_version: StrictInt | None = None


class CriticalInterruptionRead(BaseModel):
    lesson: LessonSpec
    opportunity_id: UUID
    study_session_id: str


class PlayMoveResponse(BaseModel):
    opponent_move: str | None
    status: str
    critical_interruption: CriticalInterruptionRead | None = None
    clock: PlayClockRead | None = None


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
        clock_runtime=getattr(request.app.state, "play_clock_runtime", None),
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
    # Board, status and clock must come from the same SQLite read snapshot.
    snapshot = session.exec(
        select(PlaySession, Game, PlayClock)
        .outerjoin(Game, col(Game.id) == col(PlaySession.game_id))
        .outerjoin(PlayClock, col(PlayClock.session_id) == col(PlaySession.id))
        .where(
            col(PlaySession.id) == play_session.id,
            col(PlaySession.player_id) == play_session.player_id,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="PlaySession not found")
    play_session, game, clock = snapshot
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
        sparring=read_sparring(session, play_session),
        assistance_modes=assistance_modes(session, play_session.id),
        clock=project_clock(clock) if clock is not None else None,
        pgn=game.pgn if game is not None else None,
        result=game.result if game is not None else None,
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
    source: SparringSource | None = None
    if session_in.sparring_source is not None:
        if (
            session_in.game_id is not None
            or session_in.initial_fen is not None
            or session_in.player_color is not None
            or session_in.clock_config is not None
            or session_in.coach_mode
            or session_in.opponent_config.get("provider", "stockfish") != "stockfish"
        ):
            raise HTTPException(
                status_code=422, detail="Sparring source cannot override its position or play mode"
            )
        try:
            source = load_source(session, authenticated_player_id, session_in.sparring_source)
        except SparringSourceNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except IneligibleSparringSource as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    initial_fen = source.position.fen if source else session_in.initial_fen
    config = dict(session_in.opponent_config)
    if config.get("provider", "stockfish") == "stockfish":
        config["provider"] = "stockfish"
        if "bot_level" not in config:
            player = session.get(Player, authenticated_player_id)
            manual = manual_bot_level(player) if player is not None else None
            suggestion = onboarding_for(authenticated_player_id, session).suggested_bot_level
            # Continuing an analysed/studied position retains its existing level-1 default.
            config["bot_level"] = str(
                (manual or suggestion or 1)
                if initial_fen is None and session_in.game_id is None
                else 1
            )
        config["bot_policy_version"] = BOT_POLICY_VERSION
    else:
        config.pop("bot_policy_version", None)
    # Client-supplied internal values cannot select another seed or undo boundary.
    config["seed"] = str(secrets.randbits(64))
    config["custom_start"] = (
        "true" if initial_fen is not None or session_in.game_id is not None else "false"
    )
    config["assistance_generation"] = "0"
    game = None
    if initial_fen is not None:
        board = chess.Board(initial_fen)
    elif session_in.game_id is not None:
        game = session.get(Game, session_in.game_id)
        if game is None or game.owner_player_id != authenticated_player_id:
            raise HTTPException(status_code=404, detail="Game not found")
        board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
        for move in game.moves:
            board.push_uci(move)
        active_conversion = session.exec(
            select(SparringAttempt.id)
            .join(PlaySession, col(SparringAttempt.play_session_id) == col(PlaySession.id))
            .where(PlaySession.game_id == game.id, PlaySession.status == "active")
            .limit(1)
        ).first()
        if active_conversion is not None:
            initial_fen = board.fen()
            game = None
        else:
            source_is_timed = (
                session.exec(
                    select(PlayClock)
                    .join(PlaySession, col(PlaySession.id) == col(PlayClock.session_id))
                    .where(col(PlaySession.game_id) == game.id)
                ).first()
                is not None
            )
            if session_in.clock_config is not None or source_is_timed:
                # Timed Games stay private even to later untimed continuations.
                copied_headers = dict(game.headers)
                if session_in.clock_config is None:
                    copied_headers.pop("TimeControl", None)
                game = Game(
                    pgn="",
                    headers=copied_headers,
                    moves=list(game.moves),
                    white=game.white,
                    black=game.black,
                    owner_player_id=authenticated_player_id,
                )
    else:
        board = chess.Board()
    requested_color = source.game.owner_color if source else session_in.player_color
    if requested_color == "random":
        colour = bool(secrets.randbits(1))
    elif requested_color is None:
        colour = board.turn
    else:
        colour = requested_color == "white"
    config["player_color"] = "white" if colour else "black"
    if game is None and (
        session_in.clock_config is not None or initial_fen is not None or board.turn != colour
    ):
        white, black = participants(session_in.player_id, config, initial_fen)
        game = Game(
            pgn="",
            headers={"FEN": initial_fen} if initial_fen else {},
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
        if session_in.clock_config is not None:
            control = session_in.clock_config
            game.headers = {
                **game.headers,
                "TimeControl": f"{control.initial_seconds}+{control.increment_seconds}",
            }
        if session_in.clock_config is not None or not game.pgn:
            game.pgn = build_pgn(game)
    play_session = PlaySession(
        player_id=session_in.player_id,
        game_id=game.id if game else None,
        opponent_config=config,
        clock_config=(
            {key: str(value) for key, value in session_in.clock_config.model_dump().items()}
            if session_in.clock_config is not None
            else None
        ),
        coach_mode=session_in.coach_mode,
        assistance_tracking_version=1,
    )
    if source is not None:
        session.add(play_session)
        session.flush()
        session.add(
            SparringAttempt(
                player_id=authenticated_player_id,
                source_review_id=source.review.id,
                source_position_id=source.position.id,
                source_analysis_id=source.analysis.id,
                play_session_id=play_session.id,
                source_ply=source.ply,
            )
        )
    first_move = None
    play_session.opponent_config = {
        **play_session.opponent_config,
        "initial_ply": str(len(game.moves) if game else 0),
    }
    session.add(play_session)
    if session_in.clock_config is not None:
        session.flush()
        control = session_in.clock_config
        initial_ms = control.initial_seconds * 1000
        terminal = board.is_game_over()
        session.add(
            PlayClock(
                session_id=play_session.id,
                initial_seconds=control.initial_seconds,
                increment_seconds=control.increment_seconds,
                white_remaining_ms=initial_ms,
                black_remaining_ms=initial_ms,
                active_color=None if terminal else "white" if board.turn else "black",
                deadline_at=None if terminal else utc_now() + timedelta(milliseconds=initial_ms),
                terminal_reason="board" if terminal else None,
            )
        )
        if terminal:
            service.complete_session(play_session, game, board.result() if game else None)
        else:
            session.commit()
        service.notify_clock()
    else:
        if game is not None and board.turn != colour and not board.is_game_over():
            first_move = await service.play_opponent_move(play_session, game, board)
            game.pgn = build_pgn(game)
            play_session.opponent_config = {
                **play_session.opponent_config,
                "initial_ply": str(len(game.moves)),
            }
        if board.is_game_over():
            session.flush()
            service.complete_session(play_session, game, board.result() if game else None)
        else:
            session.commit()
    schedule_pending_analysis(
        service, background_tasks, getattr(request.app.state, "engine_pool_manager", None)
    )
    session.refresh(play_session)
    return read_play_session(play_session, session, first_move)


@router.get("/v1/play-sessions/{session_id}", response_model=PlaySessionRead)
async def get_play_session(
    session_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
    after_clock_version: int | None = Query(default=None, ge=0),
) -> PlaySessionRead:
    engine = session.get_bind()
    runtime: PlayClockRuntime | None = getattr(request.app.state, "play_clock_runtime", None)
    loop = asyncio.get_running_loop()
    change_deadline: float | None = None
    change_wait_finished = False

    def read_snapshot() -> PlaySessionRead:
        # Every read owns and closes its transaction before the async wait.
        with Session(engine) as reader:
            owned = _get_owned_play_session(session_id, request, reader)
            return read_play_session(owned, reader)

    try:
        async with asyncio.timeout(CLOCK_EXPIRY_WAIT_SECONDS):
            while True:
                # Subscribe before reading so a concurrent commit cannot be missed.
                observed = runtime.change_event if runtime is not None else None
                snapshot = await asyncio.to_thread(read_snapshot)
                clock = snapshot.clock
                if (
                    snapshot.status != "active"
                    or clock is None
                    or clock.active_color is None
                    or clock.deadline_at is None
                ):
                    return snapshot
                if clock.server_now < clock.deadline_at:
                    if (
                        after_clock_version != clock.version
                        or change_wait_finished
                        or runtime is None
                        or observed is None
                        or runtime.failed
                    ):
                        return snapshot
                    if change_deadline is None:
                        change_deadline = loop.time() + CLOCK_CHANGE_WAIT_SECONDS
                    remaining_wait = change_deadline - loop.time()
                    if remaining_wait <= 0:
                        return snapshot
                    try:
                        changed = await asyncio.wait_for(
                            runtime.wait_for_change(observed), timeout=remaining_wait
                        )
                        change_wait_finished = not changed
                    except TimeoutError:
                        change_wait_finished = True
                    # Wake or bounded timeout: refresh the authoritative snapshot,
                    # including expiry handling if the deadline passed meanwhile.
                    continue
                if runtime is not None and runtime.failed:
                    raise HTTPException(
                        status_code=503, detail="Play clock scheduler is unavailable."
                    )
                if (
                    runtime is None
                    or observed is None
                    or not await runtime.wait_for_change(observed)
                ):
                    raise HTTPException(
                        status_code=503, detail="Clock expiration is pending; retry this read."
                    )
    except TimeoutError as error:
        raise HTTPException(
            status_code=503,
            detail="Clock expiration is pending; retry this read.",
            headers={"Retry-After": "1"},
        ) from error


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
    _get_owned_play_session(session_id, request, session)
    try:
        move_result = await service.make_move(
            session_id,
            move_in.move,
            move_in.expected_clock_version,
            expected_generation=move_in.expected_generation,
        )
        pool_manager = getattr(request.app.state, "engine_pool_manager", None)
        schedule_pending_analysis(service, background_tasks, pool_manager)
        interruption = move_result.interruption
        return PlayMoveResponse(
            opponent_move=move_result.opponent_move,
            status=move_result.status,
            critical_interruption=(
                CriticalInterruptionRead(
                    lesson=interruption.lesson,
                    opportunity_id=interruption.opportunity_id,
                    study_session_id=interruption.study_session_id,
                )
                if interruption is not None
                else None
            ),
            clock=move_result.clock,
        )
    except PlaySessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (PlaySessionNotActive, StalePlaySnapshot, ClockConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        schedule_pending_analysis(
            service, background_tasks, getattr(request.app.state, "engine_pool_manager", None)
        )


@router.post("/v1/play-sessions/{session_id}/resume", response_model=PlayMoveResponse)
async def resume_play_session(
    request: Request,
    session_id: UUID,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    service: PlaySessionService = Depends(get_play_session_service),
    command: PlayResumeCreate | None = None,
) -> PlayMoveResponse:
    _get_owned_play_session(session_id, request, session)
    try:
        move_result = await service.resume_bot_turn(
            session_id, command.expected_clock_version if command is not None else None
        )
        schedule_pending_analysis(
            service, background_tasks, getattr(request.app.state, "engine_pool_manager", None)
        )
        interruption = move_result.interruption
        return PlayMoveResponse(
            opponent_move=move_result.opponent_move,
            status=move_result.status,
            critical_interruption=(
                CriticalInterruptionRead(
                    lesson=interruption.lesson,
                    opportunity_id=interruption.opportunity_id,
                    study_session_id=interruption.study_session_id,
                )
                if interruption is not None
                else None
            ),
            clock=move_result.clock,
        )
    except PlaySessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (PlaySessionNotActive, StalePlaySnapshot, ClockConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    finally:
        schedule_pending_analysis(
            service, background_tasks, getattr(request.app.state, "engine_pool_manager", None)
        )


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
    except (PlaySessionNotActive, ClockConflict) as error:
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


@router.post("/v1/play-sessions/{session_id}/opponent-move", response_model=PlayMoveResponse)
async def resume_opponent_move(
    session_id: UUID,
    request: Request,
    command: OpponentMoveCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    service: PlaySessionService = Depends(get_play_session_service),
) -> PlayMoveResponse:
    _get_owned_play_session(session_id, request, session)
    try:
        move_result = await service.resume_opponent(session_id, command.expected_clock_version)
    except (ClockConflict, PlaySessionNotActive) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except PlaySessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    finally:
        schedule_pending_analysis(
            service, background_tasks, getattr(request.app.state, "engine_pool_manager", None)
        )
    return PlayMoveResponse(
        opponent_move=move_result.opponent_move,
        status=move_result.status,
        clock=move_result.clock,
    )
