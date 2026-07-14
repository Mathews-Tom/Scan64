from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scan64.api.games import router as games_router
from scan64.api.middleware import IdempotencyMiddleware
from scan64.api.play import router as play_router
from scan64.api.players import router as players_router
from scan64.api.content import router as content_router
from scan64.persistence.database import create_db_and_tables, get_session


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

    create_db_and_tables()
    yield


app = FastAPI(
    title="Scan64 API",
    version="v1",
    lifespan=lifespan,
)
app.include_router(content_router)


app.add_middleware(IdempotencyMiddleware, get_session_callable=get_session)
app.include_router(games_router)
app.include_router(players_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
