from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from sqlmodel import Session

import scan64.persistence.database as database
from scan64.api.analysis_explanation import router as analysis_explanation_router
from scan64.api.coach import router as coach_router
from scan64.api.coach_linkage import router as coach_linkage_router
from scan64.api.content import router as content_router
from scan64.api.data_lifecycle import router as data_lifecycle_router
from scan64.api.desktop_security import add_desktop_security
from scan64.api.diagnostics import router as diagnostics_router
from scan64.api.explanation_settings import router as explanation_settings_router
from scan64.api.games import router as games_router
from scan64.api.habit import router as habit_router
from scan64.api.insights import router as insights_router
from scan64.api.learning import router as learning_router
from scan64.api.lesson_pack_body import LessonPackBodyLimitMiddleware
from scan64.api.lesson_packs import router as lesson_packs_router
from scan64.api.middleware import IdempotencyMiddleware
from scan64.api.onboarding import router as onboarding_router
from scan64.api.play import router as play_router
from scan64.api.play_assistance import router as play_assistance_router
from scan64.api.players import router as players_router
from scan64.api.progress import router as progress_router
from scan64.api.puzzle_mistakes import router as puzzle_mistakes_router
from scan64.api.puzzles import router as puzzles_router
from scan64.api.repertoire import router as repertoire_router
from scan64.api.repertoire_body import RepertoireBodyLimitMiddleware
from scan64.api.reports import router as reports_router
from scan64.api.strength import router as strength_router
from scan64.api.web import mount_web_client, web_dist_directory
from scan64.chess.analysis.admission import admission_controller
from scan64.chess.analysis.notifications import close_analysis_subscriptions
from scan64.chess.analysis.recovery import recover_analysis_jobs
from scan64.chess.games.clocks import PlayClockRuntime
from scan64.content.transfer_catalog import seed_transfer_positions
from scan64.learning.plugins.host_registry import clear_host_registry, initialize_host_registry
from scan64.lesson_packs.service import fail_interrupted_packs
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
    fail_interrupted_packs(database.engine)
    app.state.lesson_pack_tasks = {}
    app.state.lesson_pack_lock = asyncio.Lock()
    app.state.plugin_registry = initialize_host_registry()
    with Session(database.engine) as session:
        seed_transfer_positions(session)
    app.state.engine_pool_manager = (
        EnginePoolManager.from_env(StockfishConfig()) if engine_pool_enabled() else None
    )
    if shell_token is not None:
        from scan64.content.puzzle_import import DEFAULT_PUZZLE_URL
        from scan64.puzzle_import_job import PuzzleImportJob

        app.state.puzzle_import_job = PuzzleImportJob(
            database.engine,
            Path(os.environ["SCAN64_DATA_DIR"]),
            # The shell forwards this local-fixture seam only in debug builds;
            # its cleared release environment always uses the pinned URL.
            download_url=os.environ.get("SCAN64_DEBUG_PUZZLE_URL", DEFAULT_PUZZLE_URL),
        )
    try:
        app.state.play_clock_runtime = PlayClockRuntime(
            database.engine, app.state.engine_pool_manager
        )
        app.state.play_clock_runtime.start()
        await recover_analysis_jobs(app.state.engine_pool_manager)
        yield
    finally:
        tasks = list(app.state.lesson_pack_tasks.values())
        # Close admission before awaiting cancellation so no new pack task escapes the drain.
        del app.state.lesson_pack_tasks
        del app.state.lesson_pack_lock
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await app.state.play_clock_runtime.stop()
        finally:
            del app.state.play_clock_runtime
            close_analysis_subscriptions(database.engine)
            if shell_token is not None:
                await asyncio.to_thread(app.state.puzzle_import_job.close)
                del app.state.puzzle_import_job
            await admission_controller.stop()
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
app.include_router(play_assistance_router)
app.include_router(learning_router)
app.include_router(insights_router)
app.include_router(analysis_explanation_router)
app.include_router(puzzles_router)
app.include_router(repertoire_router)
app.include_router(lesson_packs_router)

app.include_router(puzzle_mistakes_router)

app.add_middleware(IdempotencyMiddleware, get_session_callable=get_session)
app.add_middleware(RepertoireBodyLimitMiddleware)
app.add_middleware(LessonPackBodyLimitMiddleware)
app.include_router(games_router)
app.include_router(coach_linkage_router)
app.include_router(coach_router)
app.include_router(players_router)
app.include_router(data_lifecycle_router)
app.include_router(progress_router)
app.include_router(onboarding_router)
app.include_router(explanation_settings_router)
app.include_router(diagnostics_router)
app.include_router(strength_router)
app.include_router(reports_router)
app.include_router(habit_router)

shell_token = os.environ.get("SCAN64_SHELL_TOKEN")
add_desktop_security(app, shell_token)
if shell_token is not None:
    from scan64.api.puzzle_import import router as puzzle_import_router

    app.include_router(puzzle_import_router)


@app.get("/health")
def health(response: Response) -> dict[str, str]:
    runtime: PlayClockRuntime | None = getattr(app.state, "play_clock_runtime", None)
    if runtime is not None and runtime.failed:
        response.status_code = 503
        return {"status": "degraded", "play_clock_scheduler": "failed"}
    return {"status": "ok"}


@app.get("/v1/app/info")
def app_info() -> dict[str, str]:
    return {
        "distribution": (
            "desktop"
            if shell_token is not None or os.getenv("SCAN64_DISTRIBUTION") == "desktop"
            else "source"
        )
    }


# Registered last: known API routes retain their own methods and responses.
if shell_token is None:
    web_dist = web_dist_directory()
    if web_dist.is_dir():
        mount_web_client(app, web_dist)
