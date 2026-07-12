from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scan64.api.games import router as games_router
from scan64.persistence.database import create_db_and_tables


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    create_db_and_tables()
    yield


app = FastAPI(
    title="Scan64 API",
    version="v1",
    lifespan=lifespan,
)
app.include_router(games_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
