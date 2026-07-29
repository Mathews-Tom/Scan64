from datetime import datetime
from typing import Any
from uuid import UUID

from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import String, case, cast, func
from sqlmodel import Session, col, select

from scan64.api.auth import require_authenticated_player, require_player_token
from scan64.api.pagination import PaginatedResponse, decode_timestamp_uuid_cursor, encode_cursor
from scan64.chess.analysis.inflight import analysis_limiter
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    PersistedDiagnosis,
    PersistedLessonOpportunity,
)
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import StudySession
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import get_session

router = APIRouter(tags=["games"])


class GameCreate(BaseModel):
    pgn: str
    player_id: str


class GameRead(BaseModel):
    id: UUID
    pgn: str
    white: str
    black: str
    result: str


class AnalysisJobRead(BaseModel):
    id: UUID
    game_id: UUID
    status: str


class GameLearningSessionRead(BaseModel):
    session_id: str | None
    lessons: list[LessonSpec]
    next_cursor: str | None


def _get_owned_game(game_id: UUID, request: Request, session: Session) -> Game:
    player_id = require_authenticated_player(request, session)
    game = session.get(Game, game_id)
    if game is None or game.owner_player_id != player_id:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


def _decode_game_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        return decode_timestamp_uuid_cursor(cursor)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid cursor") from error


@router.post("/v1/games", response_model=GameRead)
def create_game(
    game_in: GameCreate, request: Request, session: Session = Depends(get_session)
) -> Game:
    import io

    import chess.pgn

    require_player_token(request, game_in.player_id, session)

    pgn_io = io.StringIO(game_in.pgn)
    chess_game = chess.pgn.read_game(pgn_io)

    if not chess_game:
        raise HTTPException(status_code=400, detail="Invalid PGN")

    game = Game(
        pgn=game_in.pgn,
        white=chess_game.headers.get("White", "Unknown"),
        black=chess_game.headers.get("Black", "Unknown"),
        result=chess_game.headers.get("Result", "*"),
        date=chess_game.headers.get("Date"),
        headers=dict(chess_game.headers),
        moves=[move.uci() for move in chess_game.mainline_moves()],
        owner_player_id=game_in.player_id,
    )

    session.add(game)
    session.commit()
    session.refresh(game)
    return game


@router.get("/v1/games", response_model=PaginatedResponse[GameRead])
def list_games(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> PaginatedResponse[GameRead]:
    player_id = require_authenticated_player(request, session)
    query = (
        select(Game)
        .where(Game.owner_player_id == player_id)
        .order_by(col(Game.created_at).desc(), col(Game.id).desc())
    )

    if cursor:
        created_at, last_id = _decode_game_cursor(cursor)
        query = query.where(
            (Game.created_at < created_at) | ((Game.created_at == created_at) & (Game.id < last_id))
        )

    query = query.limit(limit + 1)
    games = session.exec(query).all()

    next_cursor = None
    if len(games) > limit:
        next_game = games[limit - 1]
        next_cursor = encode_cursor(
            {"created_at": next_game.created_at.isoformat(), "id": str(next_game.id)}
        )
        games = games[:limit]

    game_reads = [
        GameRead(id=g.id, pgn=g.pgn, white=g.white, black=g.black, result=g.result) for g in games
    ]
    return PaginatedResponse(items=game_reads, next_cursor=next_cursor)


@router.get("/v1/games/{game_id}", response_model=GameRead)
def get_game(game_id: UUID, request: Request, session: Session = Depends(get_session)) -> Game:
    return _get_owned_game(game_id, request, session)


@router.get("/v1/games/{game_id}/learning-opportunities", response_model=GameLearningSessionRead)
def list_learning_opportunities(
    game_id: UUID,
    player_id: str,
    request: Request,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> GameLearningSessionRead:
    require_player_token(request, player_id, session)
    game = session.get(Game, game_id)
    if game is None or game.owner_player_id != player_id:
        raise HTTPException(status_code=404, detail="Owned game not found")

    query = (
        select(PersistedLessonOpportunity)
        .join(
            ReviewSchedule,
            (col(ReviewSchedule.player_id) == player_id)
            & (
                func.replace(col(ReviewSchedule.item_id), "-", "")
                == func.replace(cast(col(PersistedLessonOpportunity.id), String), "-", "")
            ),
        )
        .where(PersistedLessonOpportunity.game_id == game_id)
        .where(PersistedLessonOpportunity.player_id == player_id)
        .order_by(
            col(PersistedLessonOpportunity.created_at).desc(),
            col(PersistedLessonOpportunity.id).desc(),
        )
    )

    if cursor:
        created_at, last_id = _decode_game_cursor(cursor)
        query = query.where(
            (PersistedLessonOpportunity.created_at < created_at)
            | (
                (PersistedLessonOpportunity.created_at == created_at)
                & (PersistedLessonOpportunity.id < last_id)
            )
        )

    query = query.limit(limit + 1)
    opportunities = session.exec(query).all()

    next_cursor = None
    if len(opportunities) > limit:
        next_opp = opportunities[limit - 1]
        next_cursor = encode_cursor(
            {"created_at": next_opp.created_at.isoformat(), "id": str(next_opp.id)}
        )
        opportunities = opportunities[:limit]

    specs = []
    for opportunity in opportunities:
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
        # The opportunity UUID is the client-submittable handle for its persisted lesson.
        spec.lesson_id = str(opportunity.id)
        specs.append(spec)
    session_domain = f"game_analysis:{game_id}"
    study_session = session.exec(
        select(StudySession)
        .where(StudySession.player_id == player_id)
        .where(StudySession.domain == session_domain)
        .order_by(col(StudySession.started_at).desc())
    ).first()
    if not specs:
        return GameLearningSessionRead(
            session_id=study_session.id if study_session is not None else None,
            lessons=[],
            next_cursor=next_cursor,
        )
    if study_session is None:
        study_session = StudySession(player_id=player_id, domain=session_domain)
        session.add(study_session)
        session.commit()
        session.refresh(study_session)
    return GameLearningSessionRead(
        session_id=study_session.id,
        lessons=specs,
        next_cursor=next_cursor,
    )


@router.post("/v1/games/{game_id}/analysis-jobs", response_model=AnalysisJobRead)
def create_analysis_job(
    game_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
) -> AnalysisJob:
    game = _get_owned_game(game_id, request, session)
    owner_player_id = game.owner_player_id
    if owner_player_id is None:
        raise HTTPException(status_code=404, detail="Game not found")

    job = AnalysisJob(game_id=game_id)
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(analysis_limiter.submit, owner_player_id, job.id)
    return job


@router.get("/v1/analysis-jobs/{job_id}", response_model=AnalysisJobRead)
def get_analysis_job(
    job_id: UUID, request: Request, session: Session = Depends(get_session)
) -> AnalysisJob:
    player_id = require_authenticated_player(request, session)
    job = session.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    game = session.get(Game, job.game_id)
    if game is None or game.owner_player_id != player_id:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job


class EngineAnalysisRead(BaseModel):
    id: UUID
    config: dict[str, Any]
    raw_result: list[dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)

class GameAnalysisStatusRead(BaseModel):
    status: str


def _read_diagnosis(opportunity: PersistedLessonOpportunity) -> PersistedDiagnosis:
    return PersistedDiagnosis.model_validate(opportunity.lesson_spec.get("diagnosis"))




class PositionRead(BaseModel):
    id: UUID
    fen: str
    half_move_clock: int
    full_move_number: int
    side_to_move: str
    canonical_id: str
    analysis: EngineAnalysisRead | None = None
    diagnoses: list[PersistedDiagnosis] = []

    model_config = ConfigDict(from_attributes=True)



@router.get("/v1/games/{game_id}/analysis-status", response_model=GameAnalysisStatusRead)
def get_game_analysis_status(
    game_id: UUID, request: Request, session: Session = Depends(get_session)
) -> GameAnalysisStatusRead:
    _get_owned_game(game_id, request, session)
    analysis_job = session.exec(
        select(AnalysisJob)
        .where(AnalysisJob.game_id == game_id)
        .order_by(col(AnalysisJob.created_at).desc())
    ).first()
    return GameAnalysisStatusRead(
        status=analysis_job.status if analysis_job is not None else "not_analysed"
    )



@router.get("/v1/games/{game_id}/positions", response_model=list[PositionRead])
def get_game_positions(
    game_id: UUID, request: Request, session: Session = Depends(get_session)
) -> list[PositionRead]:
    game = _get_owned_game(game_id, request, session)
    positions = session.exec(
        select(Position)
        .where(Position.game_id == game_id)
        .order_by(
            col(Position.full_move_number),
            case((col(Position.side_to_move) == "w", 0), else_=1),
        )
    ).all()
    diagnoses_by_position: dict[UUID, list[PersistedDiagnosis]] = {
        position.id: [] for position in positions
    }
    opportunities = session.exec(
        select(PersistedLessonOpportunity).where(
            PersistedLessonOpportunity.game_id == game.id
        )
    ).all()
    for opportunity in opportunities:
        if opportunity.source_position_id is None:
            raise RuntimeError("Persisted lesson opportunity has no source position")
        try:
            diagnoses_by_position[opportunity.source_position_id].append(
                _read_diagnosis(opportunity)
            )
        except KeyError as error:
            raise RuntimeError(
                "Persisted lesson opportunity source position does not belong to its game"
            ) from error
    result: list[PositionRead] = []
    for position in positions:
        analysis = session.exec(
            select(EngineAnalysis)
            .where(EngineAnalysis.position_id == position.id)
            .order_by(col(EngineAnalysis.created_at).desc())
        ).first()
        position_data = position.model_dump()
        position_data["analysis"] = analysis
        position_data["diagnoses"] = diagnoses_by_position[position.id]
        result.append(PositionRead.model_validate(position_data))
    return result
