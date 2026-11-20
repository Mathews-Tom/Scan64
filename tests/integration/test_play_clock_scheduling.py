from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from threading import get_ident
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection, ExecutionContext
from sqlmodel import Session

import scan64.chess.games.clocks as clocks
import scan64.chess.games.play_session_service as play_service
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import PlayClockRuntime
from scan64.chess.games.models import Game, PlaySession


class WallClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 12, tzinfo=UTC)
        self._loop = asyncio.get_running_loop()
        self._loop_thread = get_ident()
        self._waits: asyncio.Queue[datetime] = asyncio.Queue()

    def utc_now(self) -> datetime:
        now = self.now
        if get_ident() == self._loop_thread:
            # Deliver only after _run has computed its delay and yielded into
            # the real asyncio wait. Worker-side UTC reads are not handshakes.
            self._loop.call_soon(self._waits.put_nowait, now)
        return now

    async def next_wait(self) -> datetime:
        return await asyncio.wait_for(self._waits.get(), timeout=3)


def _seed_waiting_clock(db: Session, now: datetime) -> tuple[UUID, UUID]:
    game = Game(
        pgn="",
        moves=[],
        headers={"TimeControl": "60+0"},
        owner_player_id="clock-owner",
        owner_color="white",
    )
    db.add(game)
    db.flush()
    play = PlaySession(
        player_id="clock-owner",
        game_id=game.id,
        opponent_config={
            "provider": "stockfish",
            "bot_level": "1",
            "player_color": "white",
            "assistance_generation": "0",
        },
        clock_config={"initial_seconds": "60", "increment_seconds": "0"},
    )
    db.add(play)
    db.flush()
    db.add(
        PlayClock(
            session_id=play.id,
            initial_seconds=60,
            increment_seconds=0,
            white_remaining_ms=60_000,
            black_remaining_ms=60_000,
            active_color="white",
            deadline_at=now + timedelta(seconds=60),
        )
    )
    session_id, game_id = play.id, game.id
    db.commit()
    return session_id, game_id


@pytest.mark.asyncio
async def test_forward_utc_jump_flags_without_waiting_for_original_monotonic_deadline(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    wall = WallClock()
    monkeypatch.setattr(clocks, "utc_now", wall.utc_now)
    monkeypatch.setattr(play_service, "utc_now", wall.utc_now)
    session_id, game_id = _seed_waiting_clock(db_session, wall.now)
    runtime = PlayClockRuntime(db_session.get_bind())
    runtime.start()
    try:
        assert await wall.next_wait() == wall.now
        observed = runtime.change_event
        # The original 60-second delay has already been computed. Do not wake
        # the scheduler: it must detect this jump through its bounded UTC wait.
        wall.now += timedelta(seconds=61)
        assert await asyncio.wait_for(runtime.wait_for_change(observed), timeout=3)
    finally:
        await runtime.stop()

    with Session(db_session.get_bind()) as session:
        play = session.get(PlaySession, session_id)
        clock = session.get(PlayClock, session_id)
        game = session.get(Game, game_id)
        assert play is not None and play.status == "completed"
        assert clock is not None
        assert clock.terminal_reason == "flag"
        assert clock.version == 1
        assert clock.active_color is None
        assert clock.deadline_at is None
        assert clock.white_remaining_ms == 0
        assert game is not None and game.result == "0-1"
        assert game.moves == []


@pytest.mark.asyncio
async def test_utc_rechecks_do_not_expire_early_or_query_until_an_actual_wake(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    wall = WallClock()
    monkeypatch.setattr(clocks, "utc_now", wall.utc_now)
    monkeypatch.setattr(play_service, "utc_now", wall.utc_now)
    session_id, _ = _seed_waiting_clock(db_session, wall.now)
    engine = db_session.get_bind()
    statements: list[str] = []

    def record_sql(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: ExecutionContext,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_sql)
    runtime = PlayClockRuntime(engine)
    runtime.start()
    try:
        assert await wall.next_wait() == wall.now
        initial_statements = statements.copy()
        assert len(initial_statements) == 1
        wall.now += timedelta(seconds=59)
        # Keep UTC frozen one second before the deadline across multiple real
        # monotonic timeouts. Neither a query nor an early flag is permitted.
        for _ in range(2):
            assert await wall.next_wait() == wall.now
            assert statements == initial_statements
        runtime.notify()
        assert await wall.next_wait() == wall.now
        assert len(statements) == len(initial_statements) + 1
    finally:
        try:
            await runtime.stop()
        finally:
            event.remove(engine, "before_cursor_execute", record_sql)

    with Session(engine) as session:
        play = session.get(PlaySession, session_id)
        clock = session.get(PlayClock, session_id)
        assert play is not None and play.status == "active"
        assert clock is not None
        assert clock.terminal_reason is None
        assert clock.version == 0
        assert clock.active_color == "white"
        assert clock.deadline_at is not None
        assert clocks.as_utc(clock.deadline_at) == wall.now + timedelta(seconds=1)
        assert clock.white_remaining_ms == 60_000
