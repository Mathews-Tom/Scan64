"""Resume durable analysis requests through normal admission on the app loop."""

from sqlmodel import Session, col, select

from scan64.chess.analysis.jobs import submit_analysis_job
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.models import Game
from scan64.persistence import database
from scan64.providers.stockfish.pool import EnginePoolManager


async def recover_analysis_jobs(pool_manager: EnginePoolManager | None) -> None:
    """Re-admit unfinished jobs; disabled pools use the normal direct adapter path."""
    with Session(database.engine) as session:
        unfinished = session.exec(
            select(AnalysisJob, Game)
            .join(Game, col(AnalysisJob.game_id) == col(Game.id))
            .where(col(AnalysisJob.status).in_(["pending", "running"]))
            .order_by(col(AnalysisJob.created_at), col(AnalysisJob.id))
        ).all()
        submissions = [(game.owner_player_id, job.id) for job, game in unfinished]
    for player_id, job_id in submissions:
        # The executor preserves the normal failure for games without an owner.
        await submit_analysis_job(player_id or "", job_id, pool_manager)
