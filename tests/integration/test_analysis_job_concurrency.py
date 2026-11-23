import asyncio
import time

import pytest
from sqlmodel import Session, create_engine, select

import scan64.chess.analysis.jobs as jobs
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.models import Game
from scan64.persistence import database


def test_bulk_import_jobs_all_complete_without_blocking_the_event_loop(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An online import queues up to 50 jobs at once. Keep the default pool capacity
    # (5 + 10 overflow) but shorten its checkout timeout so a regression fails fast.
    engine = create_engine(
        database.engine.url, connect_args={"check_same_thread": False}, pool_timeout=1
    )
    monkeypatch.setattr(database, "engine", engine)
    job_ids = []
    for _ in range(20):
        game = Game(pgn="", headers={}, moves=["e2e4"], owner_color="white")
        job = AnalysisJob(game_id=game.id)
        db_session.add_all([game, job])
        job_ids.append(job.id)
    db_session.commit()

    async def slow_analysis(*args: object, **kwargs: object) -> None:
        # Real analysis awaits engines while its job session holds a connection.
        await asyncio.sleep(0.05)

    monkeypatch.setattr(jobs, "run_analysis_for_game", slow_analysis)

    async def run_import() -> float:
        longest_stall = 0.0
        running = True

        async def heartbeat() -> None:
            nonlocal longest_stall
            last = time.monotonic()
            while running:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                longest_stall = max(longest_stall, now - last)
                last = now

        ticker = asyncio.create_task(heartbeat())
        results = await asyncio.gather(
            *(jobs.execute_analysis_job_async(job_id) for job_id in job_ids),
            return_exceptions=True,
        )
        running = False
        await ticker
        assert [result for result in results if result is not None] == []
        return longest_stall

    try:
        longest_stall = asyncio.run(run_import())
        with Session(engine) as session:
            statuses = session.exec(select(AnalysisJob.status)).all()
    finally:
        engine.dispose()
    assert statuses == ["completed"] * 20
    assert longest_stall < 0.5


def test_duplicate_jobs_for_one_game_do_not_hold_slots_from_other_games(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    busy = Game(pgn="", headers={}, moves=["e2e4"], owner_color="white")
    other = Game(pgn="", headers={}, moves=["d2d4"], owner_color="white")
    duplicates = [AnalysisJob(game_id=busy.id) for _ in range(jobs.MAX_RUNNING_ANALYSIS_JOBS + 1)]
    other_job = AnalysisJob(game_id=other.id)
    db_session.add_all([busy, other, *duplicates, other_job])
    db_session.commit()
    duplicate_ids = [job.id for job in duplicates]
    other_job_id = other_job.id
    busy_id = busy.id

    async def run() -> None:
        release = asyncio.Event()

        async def analysis(game: Game, *args: object, **kwargs: object) -> None:
            # The first job for the busy game stays in its engine work until released.
            if game.id == busy_id:
                await release.wait()

        monkeypatch.setattr(jobs, "run_analysis_for_game", analysis)
        pending = [
            asyncio.create_task(jobs.execute_analysis_job_async(job_id)) for job_id in duplicate_ids
        ]
        await asyncio.sleep(0.05)
        await asyncio.wait_for(jobs.execute_analysis_job_async(other_job_id), timeout=2)
        release.set()
        await asyncio.gather(*pending)

    asyncio.run(run())
    with Session(database.engine) as session:
        statuses = session.exec(select(AnalysisJob.status)).all()
    assert statuses == ["completed"] * (len(duplicate_ids) + 1)
