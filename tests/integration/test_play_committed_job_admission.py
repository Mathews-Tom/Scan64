from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import ORMExecuteState
from sqlmodel import Session, select

from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import PlayClockRuntime
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionNotActive, PlaySessionService
from tests.integration.test_play_clocks import FrozenTime, ScriptedOpponent, register, seed_clock
from tests.integration.test_play_clocks import frozen_time as frozen_time


@pytest.mark.asyncio
@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize("with_runtime", [False, True])
async def test_terminal_job_admission_needs_no_post_commit_refresh(
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    timed: bool,
    with_runtime: bool,
) -> None:
    register(db_session)
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))
    if not timed:
        db_session.delete(clock)
        play.clock_config = None
        db_session.add(play)
        db_session.commit()
    session_id, game_id = play.id, game.id
    engine = db_session.get_bind()
    runtime = PlayClockRuntime(engine)
    service = PlaySessionService(
        db_session, ScriptedOpponent(), clock_runtime=runtime if with_runtime else None
    )
    committed = False
    admissions: list[tuple[str, UUID]] = []
    post_commit_reads: list[ORMExecuteState] = []

    def mark_committed(session: Session) -> None:
        nonlocal committed
        committed = True

    def fail_post_commit_read(state: ORMExecuteState) -> None:
        if committed and state.is_select:
            post_commit_reads.append(state)
            raise OperationalError(
                "SELECT", {}, RuntimeError("injected post-commit refresh failure")
            )

    def admit_analysis(player_id: str, job_id: UUID) -> None:
        assert committed
        # Admission must observe durable terminal work, never an uncommitted row.
        with Session(engine) as reader:
            saved_job = reader.get(AnalysisJob, job_id)
            saved_play = reader.get(PlaySession, session_id)
            assert saved_job is not None and saved_job.game_id == game_id
            assert saved_play is not None and saved_play.status == "completed"
        admissions.append((player_id, job_id))

    monkeypatch.setattr(runtime, "admit_analysis", admit_analysis)
    event.listen(db_session, "after_commit", mark_committed)
    event.listen(db_session, "do_orm_execute", fail_post_commit_read)
    try:
        try:
            service.resign(session_id)
            assert committed
            assert post_commit_reads == []
            # The fault is armed: an actual refresh now fails. Admission already
            # happened without relying on a refresh, expired owner or clock read.
            with pytest.raises(OperationalError, match="injected post-commit refresh failure"):
                db_session.refresh(play)
        finally:
            event.remove(db_session, "after_commit", mark_committed)
            event.remove(db_session, "do_orm_execute", fail_post_commit_read)
            db_session.rollback()

        with Session(engine) as reader:
            jobs = reader.exec(select(AnalysisJob)).all()
            saved_play = reader.get(PlaySession, session_id)
            saved_game = reader.get(Game, game_id)
            saved_clock = reader.get(PlayClock, session_id)
            assert len(jobs) == 1
            assert jobs[0].game_id == game_id and jobs[0].status == "pending"
            assert saved_play is not None and saved_play.status == "completed"
            assert saved_game is not None and saved_game.result == "0-1"
            assert saved_game.moves == ["e2e4"]
            assert '[Result "0-1"]' in saved_game.pgn
            if timed:
                assert saved_clock is not None and saved_clock.version == 1
                assert saved_clock.active_color is None
                assert saved_clock.terminal_reason == "resignation"
            else:
                assert saved_clock is None
            expected = [("alice", jobs[0].id)]

        with pytest.raises(PlaySessionNotActive):
            service.resign(session_id)
        assert admissions == (expected if timed and with_runtime else [])
        assert service.pending_analysis == ([] if timed and with_runtime else expected)
        with Session(engine) as reader:
            assert len(reader.exec(select(AnalysisJob)).all()) == 1
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize("with_runtime", [False, True])
async def test_failed_terminal_commit_never_admits_analysis(
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    timed: bool,
    with_runtime: bool,
) -> None:
    register(db_session)
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))
    if not timed:
        db_session.delete(clock)
        play.clock_config = None
        db_session.add(play)
        db_session.commit()
    session_id, game_id = play.id, game.id
    engine = db_session.get_bind()
    runtime = PlayClockRuntime(engine)
    service = PlaySessionService(
        db_session, ScriptedOpponent(), clock_runtime=runtime if with_runtime else None
    )
    admissions: list[tuple[str, UUID]] = []

    def admit_analysis(player_id: str, job_id: UUID) -> None:
        admissions.append((player_id, job_id))

    def fail_commit(session: Session) -> None:
        raise OperationalError("COMMIT", {}, RuntimeError("injected terminal commit failure"))

    monkeypatch.setattr(runtime, "admit_analysis", admit_analysis)
    event.listen(db_session, "before_commit", fail_commit)
    try:
        try:
            with pytest.raises(OperationalError, match="injected terminal commit failure"):
                service.resign(session_id)
            assert admissions == []
            assert service.pending_analysis == []
        finally:
            event.remove(db_session, "before_commit", fail_commit)
            db_session.rollback()

        with Session(engine) as reader:
            assert reader.exec(select(AnalysisJob)).all() == []
            saved_play = reader.get(PlaySession, session_id)
            saved_game = reader.get(Game, game_id)
            saved_clock = reader.get(PlayClock, session_id)
            assert saved_play is not None and saved_play.status == "active"
            assert saved_game is not None and saved_game.result == "*"
            assert saved_game.moves == ["e2e4"]
            if timed:
                assert saved_clock is not None and saved_clock.version == 0
                assert saved_clock.active_color == "black"
                assert saved_clock.terminal_reason is None
            else:
                assert saved_clock is None
    finally:
        await runtime.stop()
