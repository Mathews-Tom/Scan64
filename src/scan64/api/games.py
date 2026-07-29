from typing import Any
from uuid import UUID

from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import String, case, cast, func
from sqlmodel import Session, col, select

from scan64.api.auth import require_authenticated_player, require_player_token
from scan64.api.pagination import PaginatedResponse, decode_cursor, encode_cursor
from scan64.chess.analysis.inflight import analysis_limiter
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
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


@router.post("/v1/games", response_model=GameRead)
def create_game(
    game_in: GameCreate, request: Request, session: Session = Depends(get_session)
) -> Game:
    import io

    import chess.pgn

    from scan64.api.models import Player

    require_player_token(request, game_in.player_id, session)
    if session.get(Player, game_in.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

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
    limit: int = 50,
    session: Session = Depends(get_session),
) -> PaginatedResponse[GameRead]:
    player_id = require_authenticated_player(request, session)
    query = (
        select(Game).where(Game.owner_player_id == player_id).order_by(col(Game.created_at).desc())
    )

    if cursor:
        cursor_data = decode_cursor(cursor)
        if "created_at" in cursor_data and "id" in cursor_data:
            from datetime import datetime
            from uuid import UUID

            created_at = datetime.fromisoformat(cursor_data["created_at"])
            query = query.where(
                (Game.created_at < created_at)
                | ((Game.created_at == created_at) & (Game.id < UUID(cursor_data["id"])))
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
    limit: int = 50,
    session: Session = Depends(get_session),
) -> GameLearningSessionRead:
    require_player_token(request, player_id, session)
    game = session.get(Game, game_id)
    if game is None or game.owner_player_id != player_id:
        raise HTTPException(status_code=404, detail="Owned game not found")

    limit = min(limit, 100)
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
        .order_by(col(PersistedLessonOpportunity.created_at).desc())
    )

    if cursor:
        cursor_data = decode_cursor(cursor)
        if "created_at" in cursor_data and "id" in cursor_data:
            from datetime import datetime

            created_at = datetime.fromisoformat(cursor_data["created_at"])
            query = query.where(
                (PersistedLessonOpportunity.created_at < created_at)
                | (
                    (PersistedLessonOpportunity.created_at == created_at)
                    & (PersistedLessonOpportunity.id < UUID(cursor_data["id"]))
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
    assert game.owner_player_id is not None

    job = AnalysisJob(game_id=game_id)
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(analysis_limiter.submit, game.owner_player_id, job.id)
    return job


@router.get("/v1/analysis-jobs/{job_id}", response_model=AnalysisJobRead)
def get_analysis_job(
    job_id: UUID, request: Request, session: Session = Depends(get_session)
) -> AnalysisJob:
    job = session.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    _get_owned_game(job.game_id, request, session)
    return job


class EngineAnalysisRead(BaseModel):
    id: UUID
    config: dict[str, Any]
    raw_result: list[dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)


class PositionRead(BaseModel):
    id: UUID
    fen: str
    half_move_clock: int
    full_move_number: int
    side_to_move: str
    canonical_id: str
    analysis: EngineAnalysisRead | None = None

    model_config = ConfigDict(from_attributes=True)


@router.get("/v1/games/{game_id}/positions", response_model=list[PositionRead])
def get_game_positions(
    game_id: UUID, request: Request, session: Session = Depends(get_session)
) -> list[PositionRead]:
    _get_owned_game(game_id, request, session)
    positions = session.exec(
        select(Position)
        .where(Position.game_id == game_id)
        .order_by(
            col(Position.full_move_number),
            case((col(Position.side_to_move) == "w", 0), else_=1),
        )
    ).all()
    result: list[PositionRead] = []
    for position in positions:
        analysis = session.exec(
            select(EngineAnalysis)
            .where(EngineAnalysis.position_id == position.id)
            .order_by(col(EngineAnalysis.created_at).desc())
        ).first()
        position_data = position.model_dump()
        position_data["analysis"] = analysis
        result.append(PositionRead.model_validate(position_data))
    return result
