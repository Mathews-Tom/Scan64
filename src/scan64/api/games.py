from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

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
def create_game(game_in: GameCreate, session: Session = Depends(get_session)):
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


@router.get("/v1/games/{game_id}", response_model=GameRead)
def get_game(game_id: UUID, session: Session = Depends(get_session)):
    game = session.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


def simulate_analysis_job(job_id: UUID):
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
):
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
def get_analysis_job(job_id: UUID, session: Session = Depends(get_session)):
    job = session.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job
