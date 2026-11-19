"""Server-authoritative play clocks and the app's single deadline scheduler."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Engine, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, col, select

from scan64.chess.games.clock_models import (
    ClockColor,
    ClockTerminalReason,
    PlayClock,
    PlayClockRead,
    TimeControl,
)
from scan64.chess.games.models import PlaySession

if TYPE_CHECKING:
    from scan64.providers.stockfish.pool import EnginePoolManager


logger = logging.getLogger(__name__)


class ClockConflict(ValueError):
    """A timed command no longer owns the authoritative turn."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive values even for timezone-aware columns."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def clock_control(play: PlaySession, clock: PlayClock | None) -> TimeControl | None:
    """Only a typed clock row establishes membership in a timed cohort."""
    if clock is None:
        return None
    control = TimeControl(
        initial_seconds=clock.initial_seconds, increment_seconds=clock.increment_seconds
    )
    if play.clock_config != {
        "initial_seconds": str(control.initial_seconds),
        "increment_seconds": str(control.increment_seconds),
    }:
        raise ValueError("Clock configuration does not match its persisted control")
    return control


def project_clock(clock: PlayClock, now: datetime | None = None) -> PlayClockRead:
    now = as_utc(now or utc_now())
    white, black = clock.white_remaining_ms, clock.black_remaining_ms
    deadline = as_utc(clock.deadline_at) if clock.deadline_at is not None else None
    if deadline is not None and clock.active_color is not None:
        remaining = max(0, int((deadline - now).total_seconds() * 1000))
        if clock.active_color == "white":
            white = min(white, remaining)
        else:
            black = min(black, remaining)
    return PlayClockRead(
        initial_seconds=clock.initial_seconds,
        increment_seconds=clock.increment_seconds,
        white_remaining_ms=white,
        black_remaining_ms=black,
        active_color=clock.active_color,
        deadline_at=deadline,
        server_now=now,
        version=clock.version,
        terminal_reason=clock.terminal_reason,
        flagged_color=clock.flagged_color,
    )


def is_expired(clock: PlayClock, now: datetime) -> bool:
    return clock.deadline_at is not None and as_utc(clock.deadline_at) <= as_utc(now)


def thinking_target_ms(remaining_ms: int, increment_seconds: int) -> int:
    return min(remaining_ms, 1500, max(100, remaining_ms // 30 + increment_seconds * 500))


def claim_clock(
    session: Session,
    clock: PlayClock,
    now: datetime,
    *,
    moved: bool = False,
    next_color: ClockColor | None = None,
    terminal_reason: ClockTerminalReason | None = None,
) -> None:
    """CAS a turn boundary inside the caller's snapshot/Game transaction."""
    now = as_utc(now)
    if clock.active_color is None or clock.deadline_at is None:
        raise ClockConflict("Clock is already stopped")
    projected = project_clock(clock, now)
    balances = {
        "white_remaining_ms": projected.white_remaining_ms,
        "black_remaining_ms": projected.black_remaining_ms,
    }
    if moved:
        balances[f"{clock.active_color}_remaining_ms"] += clock.increment_seconds * 1000
    values = {
        **balances,
        "version": clock.version + 1,
        "active_color": next_color,
        "deadline_at": (
            now + timedelta(milliseconds=balances[f"{next_color}_remaining_ms"])
            if next_color is not None
            else None
        ),
        "terminal_reason": terminal_reason,
        "flagged_color": clock.active_color if terminal_reason == "flag" else None,
    }
    statement = (
        update(PlayClock)
        .where(col(PlayClock.session_id) == clock.session_id)
        .where(col(PlayClock.version) == clock.version)
        .where(col(PlayClock.active_color) == clock.active_color)
        .where(col(PlayClock.deadline_at) == clock.deadline_at)
        .where(col(PlayClock.terminal_reason).is_(None))
    )
    if terminal_reason == "flag":
        statement = statement.where(col(PlayClock.deadline_at) <= now)
    else:
        statement = statement.where(col(PlayClock.deadline_at) > now)
    with session.no_autoflush:
        changed = session.exec(
            statement.values(**values).execution_options(synchronize_session=False)
        )
    if changed.rowcount != 1:
        session.rollback()
        raise ClockConflict("Clock changed or its deadline has passed")
    for name, value in values.items():
        set_committed_value(clock, name, value)


class PlayClockRuntime:
    """One event-driven deadline waiter; engine work only belongs to commands."""

    def __init__(self, engine: Engine, pool_manager: EnginePoolManager | None = None) -> None:
        self.engine = engine
        self.pool_manager = pool_manager
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._inflight: dict[UUID, asyncio.Task[object]] = {}
        self._admissions: set[asyncio.Task[None]] = set()
        self._closed = False

    def start(self) -> None:
        self._task = self._loop.create_task(self._run(), name="play-clock-deadlines")

    def notify(self) -> None:
        if not self._closed and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)

    def admit_analysis(self, player_id: str, job_id: UUID) -> None:
        """Admit durable terminal work even when the command returns HTTP 409."""
        from scan64.chess.analysis.jobs import submit_analysis_job

        def start() -> None:
            task = self._loop.create_task(submit_analysis_job(player_id, job_id, self.pool_manager))
            self._admissions.add(task)
            task.add_done_callback(self._admissions.discard)

        self._loop.call_soon_threadsafe(start)

    @contextmanager
    def reserve(self, session_id: UUID) -> Iterator[None]:
        if self._closed or session_id in self._inflight:
            raise ClockConflict("Opponent move is already in progress or runtime is stopping")
        task = asyncio.current_task()
        assert task is not None
        self._inflight[session_id] = task
        try:
            yield
        finally:
            self._inflight.pop(session_id, None)

    async def stop(self) -> None:
        self._closed = True
        tasks = [*self._inflight.values()]
        if self._task is not None:
            tasks.append(self._task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        await asyncio.gather(*self._admissions, return_exceptions=True)

    async def _run(self) -> None:
        from scan64.chess.games.play_session_service import (
            PlaySessionNotActive,
            PlaySessionNotFound,
            PlaySessionService,
        )
        from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
        from scan64.providers.stockfish.adapter import StockfishConfig

        retry_delay = 0.05
        while True:
            self._wake.clear()
            try:
                with Session(self.engine) as session:
                    clock = session.exec(
                        select(PlayClock)
                        .join(PlaySession, col(PlaySession.id) == col(PlayClock.session_id))
                        .where(col(PlaySession.status) == "active")
                        .where(col(PlayClock.active_color).is_not(None))
                        .where(col(PlayClock.deadline_at).is_not(None))
                        .order_by(col(PlayClock.deadline_at))
                        .limit(1)
                    ).first()
                    deadline = as_utc(clock.deadline_at) if clock and clock.deadline_at else None
                    session_id = clock.session_id if clock else None
                if deadline is None:
                    retry_delay = 0.05
                    await self._wake.wait()
                    continue
                delay = (deadline - utc_now()).total_seconds()
                if delay > 0:
                    retry_delay = 0.05
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=delay)
                        continue
                    except TimeoutError:
                        pass
                with Session(self.engine) as session:
                    service = PlaySessionService(
                        session,
                        StockfishOpponentProvider(StockfishConfig(), self.pool_manager),
                        engine_pool_manager=self.pool_manager,
                        clock_runtime=self,
                    )
                    try:
                        assert session_id is not None
                        service.expire_clock(session_id)
                    except (ClockConflict, PlaySessionNotActive, PlaySessionNotFound):
                        pass
                retry_delay = 0.05
            except OperationalError as error:
                code = getattr(error.orig, "sqlite_errorcode", None)
                if (
                    not isinstance(error.orig, sqlite3.OperationalError)
                    or code is None
                    or (code & 0xFF) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                ):
                    raise
                # Closing the failed Session rolls back partial expiration work.
                # Retry from a fresh deadline query, never a stale ORM snapshot.
                logger.warning("Play clock scheduler database contention; retrying", exc_info=True)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 1.0)
