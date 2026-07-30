from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlmodel import Session

import scan64.persistence.database as database
from scan64.api.coach import router as coach_router
from scan64.api.coach_linkage import router as coach_linkage_router
from scan64.api.content import router as content_router
from scan64.api.data_lifecycle import router as data_lifecycle_router
from scan64.api.games import router as games_router
from scan64.api.learning import router as learning_router
from scan64.api.middleware import IdempotencyMiddleware
from scan64.api.play import router as play_router
from scan64.api.players import router as players_router
from scan64.api.reports import router as reports_router
from scan64.content.transfer_catalog import seed_transfer_positions
from scan64.learning.plugins.host_registry import clear_host_registry, initialize_host_registry
from scan64.persistence.database import create_db_and_tables, get_session
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, engine_pool_enabled


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from scan64.api.middleware import IdempotencyRecord  # noqa: F401
    from scan64.api.models import Player, PlayerProfile  # noqa: F401
    from scan64.chess.analysis.models import (  # noqa: F401
        AnalysisJob,
        EngineAnalysis,
        PersistedLessonOpportunity,
    )
    from scan64.chess.games.models import Game, PlaySession  # noqa: F401
    from scan64.chess.positions.models import Position  # noqa: F401
    from scan64.coach.models import CoachStudentLink  # noqa: F401
    from scan64.learning.evidence.models import Evidence  # noqa: F401
    from scan64.learning.exercises.transfer import TransferPosition  # noqa: F401
    from scan64.learning.profiling.models import ProfileObservation, SkillState  # noqa: F401

    create_db_and_tables()
    app.state.plugin_registry = initialize_host_registry()
    with Session(database.engine) as session:
        seed_transfer_positions(session)
    app.state.engine_pool_manager = (
        EnginePoolManager.from_env(StockfishConfig()) if engine_pool_enabled() else None
    )
    try:
        yield
    finally:
        if app.state.engine_pool_manager is not None:
            await app.state.engine_pool_manager.close()
        clear_host_registry()
        del app.state.plugin_registry
        del app.state.engine_pool_manager


app = FastAPI(
    title="Scan64 API",
    version="v1",
    lifespan=lifespan,
)
app.include_router(content_router)
app.include_router(play_router)
app.include_router(learning_router)


app.add_middleware(IdempotencyMiddleware, get_session_callable=get_session)
app.include_router(games_router)
app.include_router(coach_linkage_router)
app.include_router(coach_router)
app.include_router(players_router)
app.include_router(data_lifecycle_router)
app.include_router(reports_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
