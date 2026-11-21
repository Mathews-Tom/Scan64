from __future__ import annotations

import asyncio
import sqlite3
from threading import Event

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

import scan64.api.play as play_api
import scan64.chess.games.clocks as clocks
from scan64.api.app import app
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import PlayClockRuntime
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from tests.integration.test_play_clock_review_api import _wait_for_read_waiter
from tests.integration.test_play_clock_review_api import isolate_review_api as isolate_review_api
from tests.integration.test_play_clocks import FrozenTime, ScriptedOpponent, register, seed_clock
from tests.integration.test_play_clocks import frozen_time as frozen_time


@pytest.mark.asyncio
async def test_failed_deadline_checks_degrade_until_success_and_release_waiting_read(
    db_session: Session, frozen_time: FrozenTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time)
    session_id = play.id
    db_session.rollback()
    loop = asyncio.get_running_loop()
    allow_failure = Event()
    fail_checks = Event()
    first_check = asyncio.Event()
    retry_delays: asyncio.Queue[float] = asyncio.Queue()
    retry_permits: asyncio.Queue[None] = asyncio.Queue()
    failed_sessions: list[Session] = []
    closed_failures: list[Session] = []
    successful_checks = 0

    class ContendedSession(Session):
        def exec(self, statement, *args, **kwargs):
            nonlocal successful_checks
            if fail_checks.is_set():
                assert allow_failure.wait(timeout=2), "GET did not resubscribe before failure"
                failed_sessions.append(self)
                original = sqlite3.OperationalError("private/database.sqlite: read failed")
                original.sqlite_errorcode = sqlite3.SQLITE_IOERR_READ
                raise OperationalError(str(statement), {}, original)
            result = super().exec(statement, *args, **kwargs)
            successful_checks += 1
            loop.call_soon_threadsafe(first_check.set)
            return result

        def close(self) -> None:
            super().close()
            if self in failed_sessions:
                assert not self.in_transaction()
                closed_failures.append(self)

    async def pause_retry(seconds: float) -> None:
        retry_delays.put_nowait(seconds)
        await retry_permits.get()

    monkeypatch.setattr(clocks, "Session", ContendedSession)
    monkeypatch.setattr(clocks.asyncio, "sleep", pause_retry)
    runtime = PlayClockRuntime(db_session.get_bind())
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime)
    entered = asyncio.Event()
    waits = 0
    original_wait = runtime.wait_for_change

    async def observe_wait(observed: asyncio.Event) -> bool:
        nonlocal waits
        waits += 1
        entered.set()
        if waits > 1:
            allow_failure.set()
        return await original_wait(observed)

    monkeypatch.setattr(runtime, "wait_for_change", observe_wait)
    pending = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            baseline = await http.get("/health")
            assert baseline.status_code == 200
            assert baseline.json() == {"status": "ok"}
            runtime.start()
            await asyncio.wait_for(first_check.wait(), timeout=2)
            pending = asyncio.create_task(
                http.get(
                    f"/v1/play-sessions/{session_id}",
                    headers=auth,
                    params={"after_clock_version": 0},
                )
            )
            await _wait_for_read_waiter(pending, entered)
            fail_checks.set()
            runtime.notify()
            for index, expected_delay in enumerate([0.05, 0.1, 0.2, 0.4, 0.8, 1, 1, 1]):
                delay = await asyncio.wait_for(retry_delays.get(), timeout=2)
                assert delay == pytest.approx(expected_delay)
                assert len(failed_sessions) == len(closed_failures) == index + 1
                assert runtime.failed
                assert runtime._task is not None and not runtime._task.done()
                health = await http.get("/health")
                assert health.status_code == 503
                assert health.json() == {"status": "degraded", "play_clock_scheduler": "failed"}
                if index == 0:
                    response = await asyncio.wait_for(pending, timeout=2)
                    assert response.status_code == 200, response.text
                    assert response.json()["clock"]["version"] == 0
                    # A change notification is not proof that deadline checking recovered.
                    observed = runtime.change_event
                    runtime.notify()
                    await asyncio.wait_for(observed.wait(), timeout=2)
                    assert runtime.failed
                if index < 7:
                    retry_permits.put_nowait(None)
            assert successful_checks == 1
            recovered = runtime.change_event
            fail_checks.clear()
            retry_permits.put_nowait(None)
            await asyncio.wait_for(recovered.wait(), timeout=2)
            assert successful_checks == 2
            assert not runtime.failed
            health = await http.get("/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["live", "moves", "resume", "overdue"])
async def test_failed_scheduler_preserves_live_canonical_reads_but_not_overdue_reads(
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    auth = register(db_session)
    play, game, _ = seed_clock(
        db_session, frozen_time, moves=("e2e4",) if state == "resume" else ()
    )
    session_id, game_id = play.id, game.id
    db_session.rollback()
    original = sqlite3.OperationalError("private/database.sqlite: no such table")
    original.sqlite_errorcode = sqlite3.SQLITE_ERROR

    class BrokenSession(Session):
        def exec(self, statement, *args, **kwargs):
            raise OperationalError(str(statement), {}, original)

    monkeypatch.setattr(clocks, "Session", BrokenSession)
    engine = db_session.get_bind()
    runtime = PlayClockRuntime(engine)
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime)
    opponent = ScriptedOpponent()
    monkeypatch.setitem(app.dependency_overrides, play_api.get_opponent_provider, lambda: opponent)
    failure = runtime.change_event
    writes: list[str] = []

    def capture_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    def unexpected_expiry(self, target_id):
        raise AssertionError("GET must never adjudicate an expired clock")

    async def unexpected_wait(observed: asyncio.Event) -> bool:
        raise AssertionError("A failed scheduler must not strand a version or expiry wait")

    runtime.start()
    try:
        await asyncio.wait_for(failure.wait(), timeout=2)
        assert runtime.failed
        monkeypatch.setattr(runtime, "wait_for_change", unexpected_wait)
        monkeypatch.setattr(PlaySessionService, "expire_clock", unexpected_expiry)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            if state in ("moves", "resume"):
                committed = await http.post(
                    f"/v1/play-sessions/{session_id}/{state}",
                    headers=auth,
                    json={
                        "expected_clock_version": 0,
                        **({"move": "e2e4"} if state == "moves" else {}),
                    },
                )
                assert committed.status_code == 200, committed.text
                version = committed.json()["clock"]["version"]
                assert version == (2 if state == "moves" else 1)
                assert len(opponent.contexts) == 1
            else:
                version = 0
            if state == "overdue":
                frozen_time.advance(60_000)
            event.listen(engine, "before_cursor_execute", capture_write)
            try:
                for params in ({}, {"after_clock_version": version}):
                    response = await asyncio.wait_for(
                        http.get(f"/v1/play-sessions/{session_id}", headers=auth, params=params),
                        timeout=2,
                    )
                    if state == "overdue":
                        assert response.status_code == 503
                        assert response.json() == {"detail": "Play clock scheduler is unavailable."}
                    else:
                        assert response.status_code == 200, response.text
                        data = response.json()
                        assert data["status"] == "active"
                        assert data["clock"]["version"] == version
                        if state in ("moves", "resume"):
                            assert "e4" in data["pgn"] and "e5" in data["pgn"]
                health = await http.get("/health")
                assert health.status_code == 503
                assert health.json() == {"status": "degraded", "play_clock_scheduler": "failed"}
            finally:
                event.remove(engine, "before_cursor_execute", capture_write)
        assert writes == []
        assert len(opponent.contexts) == (1 if state in ("moves", "resume") else 0)
        with Session(engine) as reader:
            assert reader.get(PlaySession, session_id).status == "active"
            clock = reader.get(PlayClock, session_id)
            assert clock.version == version
            assert clock.terminal_reason is None
            assert reader.get(Game, game_id).moves == (
                ["e2e4", "e7e5"] if state in ("moves", "resume") else []
            )
    finally:
        await runtime.stop()
