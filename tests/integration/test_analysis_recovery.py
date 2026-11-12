import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.api.app import app
from scan64.chess.analysis.admission import AdmissionConfig, AdmissionController
from scan64.chess.analysis.models import AnalysisJob, PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import ProfileObservation
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence import database

from .test_live_detector_coverage import (
    _FIXTURES,
    _FixtureFastPassOrchestrator,
    _FixtureFocusedPassOrchestrator,
    _registry,
)


class ProcessStopped(BaseException):
    """Model a process exit, not an ordinary failed analysis."""


@pytest.mark.parametrize("status", ["pending", "running"])
@pytest.mark.parametrize("quota", [0, 50])
def test_startup_recovers_analysis_once(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, status: str, quota: int
) -> None:
    monkeypatch.setenv("SCAN64_ENGINE_POOL_ENABLED", "0")
    controller = AdmissionController(AdmissionConfig(daily_quota_games=quota))
    monkeypatch.setattr(jobs, "admission_controller", controller)
    monkeypatch.setattr("scan64.api.app.admission_controller", controller)
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _FixtureFastPassOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FixtureFocusedPassOrchestrator)
    fixture = _FIXTURES[0]
    game = Game(
        pgn="",
        headers={"FEN": fixture["initial_fen"]},
        moves=fixture["moves"],
        owner_player_id="recovery-player",
        owner_color="white",
    )
    db_session.add(game)
    db_session.commit()
    job = AnalysisJob(game_id=game.id, status=status)
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    async def wait_for_completion() -> None:
        for _ in range(200):
            with Session(database.engine) as session:
                current = session.get(AnalysisJob, job_id)
                assert current is not None
                assert current.status != "failed", current.error
                siblings = session.exec(select(AnalysisJob)).all()
                if all(sibling.status == "completed" for sibling in siblings):
                    return
            await asyncio.sleep(0.01)
        pytest.fail("Recovered job did not complete")

    # Crash after the analysis helper's commit, before the completed marker.
    # None of its lesson/evidence/profile writes may survive this boundary.
    original = jobs.run_analysis_for_game

    async def crash_after_analysis(*args: object, **kwargs: object) -> None:
        await original(*args, registry=_registry(), **kwargs)  # type: ignore[arg-type]
        raise ProcessStopped()

    monkeypatch.setattr(jobs, "run_analysis_for_game", crash_after_analysis)
    with pytest.raises(ProcessStopped):
        asyncio.run(jobs.execute_analysis_job_async(job_id))
    with Session(database.engine) as session:
        for model in (PersistedLessonOpportunity, ReviewSchedule, ProfileObservation, Evidence):
            assert session.exec(select(model)).all() == []
        current = session.get(AnalysisJob, job_id)
        assert current is not None
        current.status = status
        session.add(current)
        session.add(AnalysisJob(game_id=game.id, status=status))
        session.commit()
    monkeypatch.setattr(jobs, "run_analysis_for_game", original)

    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(wait_for_completion)
        with Session(database.engine) as session:
            lessons = session.exec(select(PersistedLessonOpportunity)).all()
            schedules = session.exec(select(ReviewSchedule)).all()
            observations = session.exec(select(ProfileObservation)).all()
            assert len(lessons) == len(schedules) == len(observations) == 1
            assert lessons[0].game_id == game.id
            assert schedules[0].item_id == str(lessons[0].id)
            counts = {
                model: len(session.exec(select(model)).all())
                for model in (
                    PersistedLessonOpportunity,
                    ReviewSchedule,
                    ProfileObservation,
                    Evidence,
                )
            }
        # A duplicate completion request and another startup cannot add rows.
        client.portal.call(jobs.execute_analysis_job_async, job_id)
    with TestClient(app):
        with Session(database.engine) as session:
            for model, count in counts.items():
                assert len(session.exec(select(model)).all()) == count


@pytest.mark.asyncio
async def test_recovery_uses_normal_admission_and_pool(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scan64.chess.analysis import recovery

    game = Game(pgn="", owner_player_id="pool-player")
    db_session.add(game)
    db_session.commit()
    unfinished = [AnalysisJob(game_id=game.id, status=status) for status in ("pending", "running")]
    db_session.add_all(
        [
            *unfinished,
            AnalysisJob(game_id=game.id, status="completed"),
            AnalysisJob(game_id=game.id, status="failed"),
        ]
    )
    db_session.commit()
    submit = AsyncMock()
    monkeypatch.setattr(recovery, "submit_analysis_job", submit)
    pool = object()
    await recovery.recover_analysis_jobs(pool)  # type: ignore[arg-type]
    assert submit.await_count == 2
    assert {call.args[1] for call in submit.await_args_list} == {job.id for job in unfinished}
    assert all(
        call.args[0] == "pool-player" and call.args[2] is pool for call in submit.await_args_list
    )
