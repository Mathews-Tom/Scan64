from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from scan64.api.pagination import PaginatedResponse, decode_cursor, encode_cursor
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.models import Game
from scan64.persistence.database import get_session

router = APIRouter(tags=["games"])


class GameCreate(BaseModel):
    pgn: str


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


@router.post("/v1/games", response_model=GameRead)
def create_game(game_in: GameCreate, session: Session = Depends(get_session)) -> Game:
    import io

    import chess.pgn

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
    )

    session.add(game)
    session.commit()
    session.refresh(game)
    return game


@router.get("/v1/games", response_model=PaginatedResponse[GameRead])
def list_games(
    cursor: str | None = None, limit: int = 50, session: Session = Depends(get_session)
) -> PaginatedResponse[GameRead]:
    limit = min(limit, 100)
    query = select(Game).order_by(col(Game.created_at).desc())

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
        GameRead(id=g.id, pgn=g.pgn, white=g.white, black=g.black, result=g.result)
        for g in games
    ]
    return PaginatedResponse(items=game_reads, next_cursor=next_cursor)


@router.get("/v1/games/{game_id}", response_model=GameRead)
def get_game(game_id: UUID, session: Session = Depends(get_session)) -> Game:
    game = session.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


def simulate_analysis_job(job_id: UUID) -> None:
    # This is a mock function simulating a background task
    # We create a new session because background tasks run outside the request lifecycle
    import time
    from datetime import UTC, datetime

    from scan64.persistence.database import engine

    time.sleep(0.1)  # simulate work

    with Session(engine) as session:
        job = session.get(AnalysisJob, job_id)
        if job:
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            session.add(job)
            session.commit()


@router.post("/v1/games/{game_id}/analysis-jobs", response_model=AnalysisJobRead)
def create_analysis_job(
    game_id: UUID, background_tasks: BackgroundTasks, session: Session = Depends(get_session)
) -> AnalysisJob:
    game = session.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    job = AnalysisJob(game_id=game_id)
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(simulate_analysis_job, job.id)
    return job


@router.get("/v1/analysis-jobs/{job_id}", response_model=AnalysisJobRead)
def get_analysis_job(job_id: UUID, session: Session = Depends(get_session)) -> AnalysisJob:
    job = session.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job
