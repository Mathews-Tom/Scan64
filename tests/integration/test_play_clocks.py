from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import AsyncMock
from uuid import UUID

import chess
import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, select

import scan64.api.play as play_api
import scan64.api.play_position as play_position
import scan64.chess.games.assistance as assistance
import scan64.chess.games.clocks as clocks
import scan64.chess.games.play_session_service as play_service
from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import ClockConflict, PlayClockRuntime, clock_control, project_clock
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import (
    PlayMoveResult,
    PlaySessionService,
    TakebackNotAllowed,
)
from scan64.chess.opponents.protocols import MoveDecision, OpponentContext
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.persistence.database import create_database_engine
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, PooledStockfishAdapter


class FrozenTime:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 19, 12, tzinfo=UTC)

    def advance(self, milliseconds: int) -> None:
        self.now += timedelta(milliseconds=milliseconds)


class ScriptedOpponent(StockfishOpponentProvider):
    def __init__(self, move: str = "e7e5", effect=None) -> None:
        super().__init__(StockfishConfig())
        self.move = move
        self.effect = effect
        self.contexts: list[OpponentContext] = []

    async def choose_move(self, position, context: OpponentContext) -> MoveDecision:
        self.contexts.append(context)
        if self.effect is not None:
            await self.effect()
        return MoveDecision(self.move)


async def wait_for_opponent(task: asyncio.Task[PlayMoveResult], entered: asyncio.Event) -> None:
    waiting = asyncio.create_task(entered.wait())
    try:
        async with asyncio.timeout(2):
            done, _ = await asyncio.wait((task, waiting), return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
                raise AssertionError("Command completed before entering the opponent")
            await waiting
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


@pytest.fixture
def frozen_time(monkeypatch: pytest.MonkeyPatch) -> FrozenTime:
    frozen = FrozenTime()
    monkeypatch.setattr(clocks, "utc_now", lambda: frozen.now)
    monkeypatch.setattr(play_service, "utc_now", lambda: frozen.now)
    monkeypatch.setattr(play_api, "utc_now", lambda: frozen.now)
    monkeypatch.setattr(play_position, "utc_now", lambda: frozen.now)
    monkeypatch.setattr(assistance, "utc_now", lambda: frozen.now)

    async def no_delay(seconds: float) -> None:
        return None

    monkeypatch.setattr(play_service, "sleep", no_delay)
    return frozen


@pytest.fixture
def scripted_api():
    provider = ScriptedOpponent()
    app.dependency_overrides[play_api.get_opponent_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(play_api.get_opponent_provider, None)


@pytest.fixture
def clock_db_session(tmp_path: Path) -> Iterator[Session]:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'clocks.db'}")
    SQLModel.metadata.create_all(engine)
    try:
        # Configure separate writer/scheduler connections before taking locks.
        with engine.connect() as first, engine.connect() as second:
            assert first.connection is not second.connection
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()


@contextmanager
def held_sqlite_writer(engine: Engine, release: Event) -> Iterator[Future[bool]]:
    ready = Event()

    def hold() -> bool:
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            ready.set()
            # Release independently of the app loop, even on the old blocking
            # implementation, so a regression fails rather than hanging. The
            # watchdog must outlast the combined readiness/ASGI phase budgets.
            signalled = release.wait(timeout=20)
            connection.rollback()
            return signalled

    with ThreadPoolExecutor(max_workers=1) as executor:
        writer = executor.submit(hold)
        try:
            assert ready.wait(timeout=3), "SQLite writer did not acquire its lock"
            yield writer
        finally:
            release.set()
            writer.result(timeout=5)


def seed_clock(
    db: Session,
    frozen: FrozenTime,
    *,
    moves: tuple[str, ...] = (),
    fen: str | None = None,
    player_color: str = "white",
    increment: int = 2,
    remaining: int = 60_000,
) -> tuple[PlaySession, Game, PlayClock]:
    board = chess.Board(fen or chess.STARTING_FEN)
    for move in moves:
        board.push_uci(move)
    game = Game(
        pgn="",
        headers={"TimeControl": f"60+{increment}", **({"FEN": fen} if fen else {})},
        moves=list(moves),
        owner_player_id="alice",
        owner_color=player_color,
    )
    db.add(game)
    db.flush()
    play = PlaySession(
        player_id="alice",
        game_id=game.id,
        assistance_tracking_version=1,
        opponent_config={
            "provider": "stockfish",
            "bot_level": "1",
            "player_color": player_color,
            "assistance_generation": "0",
        },
        clock_config={"initial_seconds": "60", "increment_seconds": str(increment)},
    )
    db.add(play)
    db.flush()
    clock = PlayClock(
        session_id=play.id,
        initial_seconds=60,
        increment_seconds=increment,
        white_remaining_ms=remaining,
        black_remaining_ms=remaining,
        active_color="white" if board.turn else "black",
        deadline_at=frozen.now + timedelta(milliseconds=remaining),
    )
    db.add(clock)
    db.commit()
    return play, game, clock


def register(db: Session, player_id: str = "alice") -> dict[str, str]:
    token, token_hash = issue_player_token()
    db.add(Player(id=player_id))
    db.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def start(client: TestClient, auth: dict[str, str], **extra):
    response = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "alice",
            "clock_config": {
                "initial_seconds": 60,
                "increment_seconds": 2,
            },
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_projection_clamps_and_normalizes_sqlite_datetime(db_session, frozen_time):
    play, _, clock = seed_clock(db_session, frozen_time)
    clock.deadline_at = frozen_time.now.replace(tzinfo=None) + timedelta(seconds=60)
    frozen_time.advance(1250)
    view = project_clock(clock)
    assert view.white_remaining_ms == 58_750
    assert view.black_remaining_ms == 60_000
    assert view.deadline_at.tzinfo == UTC
    assert view.server_now.tzinfo == UTC
    frozen_time.advance(100_000)
    assert project_clock(clock).white_remaining_ms == 0
    assert clock_control(play, clock).initial_seconds == 60
    assert clock_control(play, None) is None
    play.clock_config = {"initial_seconds": "61", "increment_seconds": "2"}
    with pytest.raises(ValueError, match="does not match"):
        clock_control(play, clock)


@pytest.mark.asyncio
async def test_human_boundary_and_both_increments_are_durable(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id, game_id = play.id, game.id
    frozen_time.advance(2000)

    async def inspect_boundary():
        assert not db_session.in_transaction()
        with Session(db_session.get_bind()) as other:
            assert other.get(Game, game_id).moves == ["e2e4"]
            saved = other.get(PlayClock, session_id)
            assert saved.version == 1
            assert saved.active_color == "black"
            assert saved.white_remaining_ms == 60_000
        frozen_time.advance(1000)

    opponent = ScriptedOpponent(effect=inspect_boundary)
    result = await PlaySessionService(db_session, opponent).make_move(session_id, "e2e4", 0)
    assert result.opponent_move == "e7e5"
    db_session.refresh(clock)
    db_session.refresh(game)
    assert clock.version == 2
    assert clock.active_color == "white"
    assert clock.white_remaining_ms == 60_000
    assert clock.black_remaining_ms == 61_000
    assert game.moves == ["e2e4", "e7e5"]
    assert opponent.contexts[0].time_remaining_ms == 60_000
    assert opponent.contexts[0].bot_level == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, -1, 1])
async def test_version_is_required_before_engine_work(db_session, frozen_time, version):
    play, game, clock = seed_clock(db_session, frozen_time)
    opponent = ScriptedOpponent()
    with pytest.raises(ClockConflict, match="version"):
        await PlaySessionService(db_session, opponent).make_move(play.id, "e2e4", version)
    assert opponent.contexts == []
    assert game.moves == []
    assert clock.version == 0


@pytest.mark.asyncio
async def test_engine_failure_keeps_human_move_for_explicit_resume(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id = play.id

    async def fail():
        raise RuntimeError("engine unavailable")

    with pytest.raises(RuntimeError, match="engine unavailable"):
        await PlaySessionService(db_session, ScriptedOpponent(effect=fail)).make_move(
            session_id, "e2e4", 0
        )
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert clock.active_color == "black"
    assert clock.version == 1
    frozen_time.advance(3000)
    service = PlaySessionService(db_session, ScriptedOpponent())
    assert (await service.resume_opponent(session_id, 1)).opponent_move == "e7e5"
    db_session.refresh(clock)
    assert clock.black_remaining_ms == 59_000
    assert clock.version == 2


@pytest.mark.asyncio
async def test_provider_timeout_is_not_misreported_as_flag(db_session, frozen_time):
    play, _, clock = seed_clock(db_session, frozen_time)

    async def fail():
        raise TimeoutError("provider budget exhausted")

    with pytest.raises(TimeoutError, match="provider budget"):
        await PlaySessionService(db_session, ScriptedOpponent(effect=fail)).make_move(
            play.id, "e2e4", 0
        )
    db_session.refresh(clock)
    assert clock.active_color == "black"
    assert clock.terminal_reason is None


@pytest.mark.asyncio
async def test_cancellation_leaves_restartable_bot_turn(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id = play.id
    entered = asyncio.Event()

    async def block():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        PlaySessionService(db_session, ScriptedOpponent(effect=block)).make_move(
            session_id, "e2e4", 0
        )
    )
    try:
        await wait_for_opponent(task, entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert clock.version == 1
    result = await PlaySessionService(db_session, ScriptedOpponent()).resume_opponent(session_id, 1)
    assert result.opponent_move == "e7e5"


@pytest.mark.asyncio
async def test_duplicate_resume_does_not_start_duplicate_engine(db_session, frozen_time):
    play, _, _ = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    runtime = PlayClockRuntime(db_session.get_bind())
    entered, release = asyncio.Event(), asyncio.Event()

    async def block():
        entered.set()
        await release.wait()

    opponent = ScriptedOpponent(effect=block)
    service = PlaySessionService(db_session, opponent, clock_runtime=runtime)
    task = asyncio.create_task(service.resume_opponent(session_id, 0))
    try:
        await wait_for_opponent(task, entered)
        with Session(db_session.get_bind()) as other:
            duplicate = PlaySessionService(other, opponent, clock_runtime=runtime)
            with pytest.raises(ClockConflict, match="already in progress"):
                await duplicate.resume_opponent(session_id, 0)
        assert len(opponent.contexts) == 1
        release.set()
        async with asyncio.timeout(2):
            assert (await task).opponent_move == "e7e5"
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.stop()


@pytest.mark.asyncio
async def test_late_engine_result_cannot_overwrite_resignation(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id = play.id

    async def resign_elsewhere():
        with Session(db_session.get_bind()) as other:
            PlaySessionService(other, ScriptedOpponent()).resign(session_id)

    with pytest.raises(ClockConflict):
        await PlaySessionService(db_session, ScriptedOpponent(effect=resign_elsewhere)).make_move(
            session_id, "e2e4", 0
        )
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert game.result == "0-1"
    assert clock.terminal_reason == "resignation"
    assert clock.version == 2
    assert (
        len(db_session.exec(select(AnalysisJob).where(AnalysisJob.game_id == game.id)).all()) == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_reason", ["resignation", "flag"])
async def test_stopped_bot_clock_after_human_commit_conflicts_before_engine_work(
    db_session, frozen_time, monkeypatch, terminal_reason
):
    play, game, clock = seed_clock(db_session, frozen_time)
    play.coach_mode = True
    db_session.add(play)
    db_session.commit()
    session_id = play.id
    opponent = ScriptedOpponent()
    service = PlaySessionService(db_session, opponent)
    commit_move = service.commit_timed_move
    coach_calls = []

    async def prepare(**kwargs):
        coach_calls.append(kwargs)
        raise AssertionError("A stopped bot turn must not start coaching")

    def commit_then_stop(*args):
        commit_move(*args)
        with Session(db_session.get_bind()) as other:
            terminal_service = PlaySessionService(other, ScriptedOpponent())
            if terminal_reason == "resignation":
                terminal_service.resign(session_id)
            else:
                frozen_time.advance(60_000)
                terminal_service.expire_clock(session_id)

    monkeypatch.setattr(service, "commit_timed_move", commit_then_stop)
    monkeypatch.setattr(play_service, "prepare_coach_interruption", prepare)
    with pytest.raises(ClockConflict, match="completed"):
        await service.make_move(session_id, "e2e4", 0)
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert game.result == ("0-1" if terminal_reason == "resignation" else "1-0")
    assert clock.terminal_reason == terminal_reason
    assert clock.deadline_at is None
    assert clock.active_color is None
    assert clock.version == 2
    assert opponent.contexts == []
    assert coach_calls == []
    assert len(db_session.exec(select(AnalysisJob)).all()) == 1


@pytest.mark.asyncio
async def test_result_at_exact_deadline_flags_without_publishing_bot_move(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))

    async def arrive_late():
        frozen_time.advance(60_000)

    with pytest.raises(ClockConflict, match="deadline"):
        await PlaySessionService(db_session, ScriptedOpponent(effect=arrive_late)).resume_opponent(
            play.id, 0
        )
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert game.result == "1-0"
    assert clock.black_remaining_ms == 0
    assert clock.flagged_color == "black"
    assert clock.active_color is None


@pytest.mark.asyncio
async def test_real_bot_timeout_is_bounded_and_flags(db_session, frozen_time):
    play, game, clock = seed_clock(
        db_session, frozen_time, moves=("e2e4",), remaining=10, increment=0
    )

    async def never_finishes():
        try:
            await asyncio.Event().wait()
        finally:
            frozen_time.advance(10)

    async with asyncio.timeout(2):
        with pytest.raises(ClockConflict, match="deadline"):
            await PlaySessionService(
                db_session, ScriptedOpponent(effect=never_finishes)
            ).resume_opponent(play.id, 0)
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert clock.terminal_reason == "flag"
    assert clock.version == 1


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        (chess.STARTING_FEN, "0-1"),
        ("7k/8/8/8/8/8/6R1/K7 w - - 0 1", "1/2-1/2"),
    ],
)
def test_human_flag_and_material_specific_draw(db_session, frozen_time, fen, expected):
    play, game, clock = seed_clock(db_session, frozen_time, fen=fen)
    frozen_time.advance(60_000)
    service = PlaySessionService(db_session, ScriptedOpponent())
    service.resign(play.id)  # Expiration wins over the requested resignation.
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.result == expected
    assert clock.terminal_reason == "flag"
    assert clock.flagged_color == "white"
    assert clock.white_remaining_ms == 0
    assert clock.deadline_at is None


@pytest.mark.asyncio
async def test_terminal_human_move_stops_clock_atomically(db_session, frozen_time):
    play, game, clock = seed_clock(
        db_session, frozen_time, moves=("f2f3", "e7e5", "g2g4"), player_color="black"
    )
    opponent = ScriptedOpponent()
    result = await PlaySessionService(db_session, opponent).make_move(play.id, "d8h4", 0)
    assert result.opponent_move is None
    assert opponent.contexts == []
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.result == "0-1"
    assert clock.terminal_reason == "board"
    assert clock.active_color is None
    assert clock.black_remaining_ms == 62_000
    assert clock.version == 1


def test_timed_takeback_never_changes_clock_or_moves(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4", "e7e5"))
    with pytest.raises(TakebackNotAllowed, match="timed"):
        PlaySessionService(db_session, ScriptedOpponent()).takeback(play.id)
    assert game.moves == ["e2e4", "e7e5"]
    assert clock.version == 0


@pytest.mark.asyncio
async def test_legacy_static_clock_json_is_untimed(db_session):
    game = Game(pgn="", moves=[], owner_player_id="alice")
    db_session.add(game)
    db_session.flush()
    play = PlaySession(
        player_id="alice",
        game_id=game.id,
        clock_config={"time_remaining_ms": "1"},
        opponent_config={"player_color": "white"},
    )
    db_session.add(play)
    db_session.commit()
    opponent = ScriptedOpponent()
    await PlaySessionService(db_session, opponent).make_move(play.id, "e2e4")
    assert opponent.contexts[0].time_remaining_ms is None
    assert db_session.get(PlayClock, play.id) is None


@pytest.mark.asyncio
async def test_runtime_expires_without_browser_and_admits_analysis(
    clock_db_session, frozen_time, monkeypatch
):
    play, game, clock = seed_clock(clock_db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    frozen_time.advance(60_000)
    admitted = asyncio.Event()

    async def submit(player_id, job_id, pool):
        assert player_id == "alice"
        admitted.set()

    monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
    runtime = PlayClockRuntime(clock_db_session.get_bind())
    runtime.start()
    try:
        await asyncio.wait_for(admitted.wait(), timeout=1)
        with Session(clock_db_session.get_bind()) as other:
            assert other.get(PlaySession, session_id).status == "completed"
            assert other.get(PlayClock, session_id).terminal_reason == "flag"
            assert other.get(Game, game.id).moves == ["e2e4"]
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_sqlite_writer_contention_keeps_asgi_health_responsive(
    clock_db_session, frozen_time, monkeypatch
):
    auth = register(clock_db_session)
    play, game, _ = seed_clock(clock_db_session, frozen_time, moves=("e2e4",))
    session_id, game_id = play.id, game.id
    frozen_time.advance(60_000)
    engine = clock_db_session.get_bind()
    monkeypatch.setattr("scan64.persistence.database.engine", engine)
    snapshot_read = Event()
    original_read = play_api.read_play_session

    def capture_snapshot(*args, **kwargs):
        snapshot = original_read(*args, **kwargs)
        snapshot_read.set()
        return snapshot

    monkeypatch.setattr(play_api, "read_play_session", capture_snapshot)
    attempted, release_writer = Event(), Event()
    admitted = asyncio.Event()
    engine_calls = []
    request_loop = asyncio.get_running_loop()

    def capture_write(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE"):
            attempted.set()

    async def submit(player_id, job_id, pool):
        assert asyncio.get_running_loop() is request_loop
        admitted.set()

    async def unexpected_engine(self, position, context):
        engine_calls.append(context)
        raise AssertionError("Deadline/status work must never choose an opponent move")

    monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
    monkeypatch.setattr(StockfishOpponentProvider, "choose_move", unexpected_engine)
    event.listen(engine, "before_cursor_execute", capture_write)
    runtime = PlayClockRuntime(engine)
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime, raising=False)
    try:
        with held_sqlite_writer(engine, release_writer) as writer:
            runtime.start()
            try:
                assert await asyncio.wait_for(asyncio.to_thread(attempted.wait, 2), timeout=3), (
                    "Expiration did not attempt its SQLite write"
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    deadline_read: asyncio.Task[httpx.Response] | None = None
                    try:
                        response = await asyncio.wait_for(client.get("/health"), timeout=2)
                        assert response.status_code == 200
                        assert response.json() == {"status": "ok"}
                        deadline_read = asyncio.create_task(
                            client.get(f"/v1/play-sessions/{session_id}", headers=auth)
                        )
                        assert await asyncio.wait_for(
                            asyncio.to_thread(snapshot_read.wait, 1), timeout=2
                        ), "The deadline GET did not read its overdue snapshot"
                        # Release only after the GET saw the still-active, zero clock.
                        release_writer.set()
                        assert await asyncio.wrap_future(writer), (
                            "Writer released before health and the deadline read"
                        )
                        await asyncio.wait_for(admitted.wait(), timeout=2)
                        final = await asyncio.wait_for(deadline_read, timeout=2)
                        assert final.status_code == 200
                        assert final.json()["status"] == "completed"
                        assert final.json()["clock"]["terminal_reason"] == "flag"
                        assert final.json()["clock"]["version"] == 1
                        assert final.json()["result"] == "1-0"
                    finally:
                        release_writer.set()
                        if deadline_read is not None:
                            if not deadline_read.done():
                                deadline_read.cancel()
                            await asyncio.gather(deadline_read, return_exceptions=True)
            finally:
                release_writer.set()
                await runtime.stop()
        with Session(engine) as session:
            assert session.get(PlaySession, session_id).status == "completed"
            clock = session.get(PlayClock, session_id)
            assert clock.terminal_reason == "flag"
            assert clock.version == 1
            assert session.get(Game, game_id).moves == ["e2e4"]
            assert len(session.exec(select(AnalysisJob)).all()) == 1
        assert engine_calls == []
    finally:
        event.remove(engine, "before_cursor_execute", capture_write)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interruption", "advance_us"),
    [
        ("timeout", 60_000_000),
        ("live", 59_999_500),
        ("shutdown", 60_000_000),
        ("cancel", 60_000_000),
    ],
)
async def test_overdue_read_interruptions_are_explicit_and_do_not_prevent_expiry(
    clock_db_session, frozen_time, monkeypatch, interruption, advance_us
):
    auth = register(clock_db_session)
    play, _, _ = seed_clock(clock_db_session, frozen_time)
    session_id = play.id
    frozen_time.now += timedelta(microseconds=advance_us)
    engine = clock_db_session.get_bind()
    monkeypatch.setattr("scan64.persistence.database.engine", engine)
    runtime = PlayClockRuntime(engine)
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime, raising=False)
    entered = asyncio.Event()
    original_wait = runtime.wait_for_change

    async def observe_wait(observed):
        entered.set()
        return await original_wait(observed)

    async def submit(player_id, job_id, pool):
        return None

    monkeypatch.setattr(runtime, "wait_for_change", observe_wait)
    monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
    if interruption == "timeout":
        monkeypatch.setattr(play_api, "CLOCK_EXPIRY_WAIT_SECONDS", 1.0)
    pending = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.get(f"/v1/play-sessions/{session_id}", headers=auth)
            )
            if interruption in ("shutdown", "cancel"):
                await asyncio.wait_for(entered.wait(), timeout=2)
            if interruption == "shutdown":
                await runtime.stop()
            if interruption == "cancel":
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                runtime.start()
                final = await asyncio.wait_for(
                    client.get(f"/v1/play-sessions/{session_id}", headers=auth), timeout=2
                )
                assert final.status_code == 200
                assert final.json()["status"] == "completed"
                assert final.json()["clock"]["terminal_reason"] == "flag"
            elif interruption == "live":
                response = await asyncio.wait_for(pending, timeout=2)
                assert response.status_code == 200, response.text
                assert response.json()["status"] == "active"
                assert response.json()["clock"]["white_remaining_ms"] == 0
                assert response.json()["clock"]["version"] == 0
                assert not entered.is_set()
            else:
                response = await asyncio.wait_for(pending, timeout=2)
                assert response.status_code == 503
                assert "expiration is pending" in response.json()["detail"]
                if interruption == "timeout":
                    assert response.headers["Retry-After"] == "1"
                with Session(engine) as reader:
                    assert reader.get(PlaySession, session_id).status == "active"
                    assert reader.get(PlayClock, session_id).version == 0
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", ["shutdown", "scheduler", "stop"])
async def test_runtime_shutdown_drains_sqlite_worker_and_terminal_admissions(
    clock_db_session, frozen_time, monkeypatch, cancellation
):
    play, game, _ = seed_clock(clock_db_session, frozen_time, moves=("e2e4",))
    session_id, game_id = play.id, game.id
    frozen_time.advance(60_000)
    engine = clock_db_session.get_bind()
    attempted, release_writer = Event(), Event()
    admission_started, release_admission = asyncio.Event(), asyncio.Event()
    expiry_closed = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop_thread = get_ident()
    sessions, closed, providers, engine_calls, admissions = [], [], [], [], []

    class WorkerSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.owner_thread = get_ident()
            self.wrote = False
            sessions.append(self)
            assert len(sessions) - len(closed) == 1

        def exec(self, statement, *args, **kwargs):
            assert get_ident() == self.owner_thread != loop_thread
            if statement.is_update:
                self.wrote = True
            return super().exec(statement, *args, **kwargs)

        def close(self):
            assert get_ident() == self.owner_thread != loop_thread
            super().close()
            closed.append(self)
            if self.wrote:
                loop.call_soon_threadsafe(expiry_closed.set)

    class WorkerOpponent(StockfishOpponentProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            providers.append(get_ident())

        async def choose_move(self, position, context):
            engine_calls.append(context)
            raise AssertionError("Expiration must not invoke an opponent engine")

    def capture_write(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE"):
            attempted.set()

    async def submit(player_id, job_id, pool):
        assert asyncio.get_running_loop() is loop
        admission_started.set()
        await release_admission.wait()
        admissions.append((player_id, job_id))

    monkeypatch.setattr(clocks, "Session", WorkerSession)
    monkeypatch.setattr(
        "scan64.chess.opponents.stockfish_opponent.StockfishOpponentProvider", WorkerOpponent
    )
    monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
    event.listen(engine, "before_cursor_execute", capture_write)
    runtime = PlayClockRuntime(engine)
    stopping = None
    try:
        with held_sqlite_writer(engine, release_writer) as writer:
            runtime.start()
            try:
                assert await asyncio.wait_for(asyncio.to_thread(attempted.wait, 2), timeout=3)
                if cancellation == "scheduler":
                    runtime._task.cancel()
                stopping = asyncio.create_task(runtime.stop())
                stopped_turn = loop.create_future()
                loop.call_soon(stopped_turn.set_result, None)
                await stopped_turn
                assert runtime._closed
                if cancellation == "stop":
                    stopping.cancel()
                assert not stopping.done()
                assert not expiry_closed.is_set()
                release_writer.set()
                assert await asyncio.wrap_future(writer), "Writer released before shutdown began"
                await asyncio.wait_for(expiry_closed.wait(), timeout=2)
                await asyncio.wait_for(admission_started.wait(), timeout=2)
                assert not stopping.done()
                assert sessions == closed
                release_admission.set()
                if cancellation == "stop":
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(stopping, timeout=2)
                else:
                    await asyncio.wait_for(stopping, timeout=2)
                assert runtime._task.cancelled()
            finally:
                release_writer.set()
                release_admission.set()
                await runtime.stop()
                if stopping is not None:
                    await asyncio.gather(stopping, return_exceptions=True)
        assert len(sessions) == 2
        assert sessions == closed
        assert providers and all(thread != loop_thread for thread in providers)
        assert engine_calls == []
        with Session(engine) as session:
            assert session.get(PlaySession, session_id).status == "completed"
            assert session.get(PlayClock, session_id).terminal_reason == "flag"
            assert session.get(Game, game_id).result == "1-0"
            jobs = session.exec(select(AnalysisJob)).all()
            assert len(jobs) == 1
            assert admissions == [("alice", jobs[0].id)]
    finally:
        event.remove(engine, "before_cursor_execute", capture_write)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "race_error",
    [ClockConflict, play_service.PlaySessionNotActive, play_service.PlaySessionNotFound],
)
async def test_runtime_requeries_after_expiration_races(
    clock_db_session, frozen_time, monkeypatch, race_error
):
    play, _, _ = seed_clock(clock_db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    frozen_time.advance(60_000)
    expire = PlaySessionService.expire_clock
    attempts = []
    admitted = asyncio.Event()

    def race_once(service, target_id):
        attempts.append(target_id)
        if len(attempts) == 1:
            raise race_error("Concurrent terminal change")
        return expire(service, target_id)

    async def submit(player_id, job_id, pool):
        admitted.set()

    monkeypatch.setattr(PlaySessionService, "expire_clock", race_once)
    monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
    runtime = PlayClockRuntime(clock_db_session.get_bind())
    runtime.start()
    try:
        await asyncio.wait_for(admitted.wait(), timeout=2)
        assert attempts == [session_id, session_id]
    finally:
        await runtime.stop()
    with Session(clock_db_session.get_bind()) as session:
        assert session.get(PlayClock, session_id).version == 1
        assert session.get(PlaySession, session_id).status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["query", "write"])
@pytest.mark.parametrize(
    "error_code",
    [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED_SHAREDCACHE, sqlite3.SQLITE_IOERR_READ],
)
async def test_runtime_recovers_sqlite_contention_with_fresh_sessions_and_bounded_backoff(
    clock_db_session, frozen_time, monkeypatch, failure_stage, error_code
):
    play, game, _ = seed_clock(clock_db_session, frozen_time, moves=("e2e4",))
    session_id, game_id = play.id, game.id
    frozen_time.advance(60_000)
    failed_sessions = []
    closed_failures = []
    delays = []
    admitted = asyncio.Event()
    real_sleep = asyncio.sleep

    class ContendedSession(Session):
        def exec(self, statement, *args, **kwargs):
            fail_query = failure_stage == "query" and statement.is_select
            fail_write = (
                failure_stage == "write"
                and statement.is_update
                and statement.table.name == "playclock"
            )
            if len(failed_sessions) < 8 and (fail_query or fail_write):
                failed_sessions.append(self)
                original = sqlite3.OperationalError("database is locked")
                original.sqlite_errorcode = error_code
                raise OperationalError(str(statement), {}, original)
            return super().exec(statement, *args, **kwargs)

        def close(self):
            super().close()
            if self in failed_sessions:
                assert not self.in_transaction()
                closed_failures.append(self)

    async def backoff(seconds):
        delays.append(seconds)
        # Every failed query/write must release its transaction before retry.
        assert closed_failures[-1] is failed_sessions[-1]
        await real_sleep(0)

    async def submit(player_id, job_id, pool):
        admitted.set()

    monkeypatch.setattr(clocks, "Session", ContendedSession)
    monkeypatch.setattr(clocks.asyncio, "sleep", backoff)
    monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
    runtime = PlayClockRuntime(clock_db_session.get_bind())
    runtime.start()
    try:
        await asyncio.wait_for(admitted.wait(), timeout=1)
        assert delays == pytest.approx([0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0])
        assert len({id(session) for session in failed_sessions}) == 8
        with Session(clock_db_session.get_bind()) as other:
            assert other.get(PlaySession, session_id).status == "completed"
            assert other.get(PlayClock, session_id).terminal_reason == "flag"
            assert other.get(PlayClock, session_id).version == 1
            assert other.get(Game, game_id).moves == ["e2e4"]
            assert len(other.exec(select(AnalysisJob)).all()) == 1
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "original",
    [
        sqlite3.OperationalError("no such table: playclock"),
        RuntimeError("non-SQLite database is locked"),
    ],
)
async def test_runtime_does_not_retry_nonretryable_database_failures(
    db_session, monkeypatch, original
):
    if isinstance(original, sqlite3.OperationalError):
        original.sqlite_errorcode = sqlite3.SQLITE_ERROR
    else:
        original.sqlite_errorcode = sqlite3.SQLITE_BUSY
    failure = OperationalError("SELECT", {}, original)
    attempts = []

    class BrokenSession(Session):
        def exec(self, statement, *args, **kwargs):
            attempts.append(statement)
            raise failure

    monkeypatch.setattr(clocks, "Session", BrokenSession)
    runtime = PlayClockRuntime(db_session.get_bind())
    runtime.start()
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime, raising=False)
    try:
        with pytest.raises(OperationalError) as raised:
            await asyncio.wait_for(asyncio.shield(runtime._task), timeout=2)
        assert raised.value is failure
        assert len(attempts) == 1
        assert runtime.failed
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            health = await client.get("/health")
        assert health.status_code == 503
        assert health.json() == {"status": "degraded", "play_clock_scheduler": "failed"}
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_immediate_shutdown_cancels_before_query(db_session, monkeypatch):
    queries = []

    class UnexpectedSession(Session):
        def exec(self, statement, *args, **kwargs):
            queries.append(statement)
            raise AssertionError("An immediately cancelled scheduler must not query")

    monkeypatch.setattr(clocks, "Session", UnexpectedSession)
    runtime = PlayClockRuntime(db_session.get_bind())
    runtime.start()
    await runtime.stop()
    assert runtime._task.cancelled()
    assert queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["query", "write"])
async def test_runtime_shutdown_cancels_sqlite_retry_backoff(
    clock_db_session, frozen_time, monkeypatch, failure_stage
):
    play, _, _ = seed_clock(clock_db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    frozen_time.advance(60_000)
    retrying = asyncio.Event()

    class LockedSession(Session):
        def exec(self, statement, *args, **kwargs):
            if failure_stage == "query" or (
                statement.is_update and statement.table.name == "playclock"
            ):
                original = sqlite3.OperationalError("database is locked")
                original.sqlite_errorcode = sqlite3.SQLITE_LOCKED
                raise OperationalError(str(statement), {}, original)
            return super().exec(statement, *args, **kwargs)

    async def wait_in_backoff(seconds):
        retrying.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(clocks, "Session", LockedSession)
    monkeypatch.setattr(clocks.asyncio, "sleep", wait_in_backoff)
    runtime = PlayClockRuntime(clock_db_session.get_bind())
    runtime.start()
    try:
        await asyncio.wait_for(retrying.wait(), timeout=1)
    finally:
        await runtime.stop()
    assert runtime._task.cancelled()
    with Session(clock_db_session.get_bind()) as other:
        assert other.get(PlaySession, session_id).status == "active"
        assert other.get(PlayClock, session_id).version == 0
        assert other.exec(select(AnalysisJob)).all() == []


@pytest.mark.asyncio
async def test_runtime_restart_does_not_run_pending_engine_and_shutdown_cancels_work(
    clock_db_session, frozen_time, monkeypatch
):
    play, game, clock = seed_clock(clock_db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    read_done = asyncio.Event()
    loop = asyncio.get_running_loop()

    class RestartSession(Session):
        def close(self):
            super().close()
            loop.call_soon_threadsafe(read_done.set)

    monkeypatch.setattr(clocks, "Session", RestartSession)
    runtime = PlayClockRuntime(clock_db_session.get_bind())
    runtime.start()
    try:
        await asyncio.wait_for(read_done.wait(), timeout=2)
    finally:
        await runtime.stop()
    clock_db_session.refresh(clock)
    assert clock.version == 0
    assert clock.active_color == "black"
    restarted = PlayClockRuntime(clock_db_session.get_bind())
    restarted.start()
    entered = asyncio.Event()

    async def wait_forever():
        entered.set()
        await asyncio.Event().wait()

    service = PlaySessionService(
        clock_db_session, ScriptedOpponent(effect=wait_forever), clock_runtime=restarted
    )
    task = asyncio.create_task(service.resume_opponent(session_id, 0))
    try:
        await wait_for_opponent(task, entered)
        await restarted.stop()
        assert task.cancelled()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await restarted.stop()
    clock_db_session.refresh(game)
    assert game.moves == ["e2e4"]


def test_timed_creation_and_opponent_first_start(client, db_session, frozen_time, scripted_api):
    auth = register(db_session)
    scripted_api.move = "e2e4"
    created = start(client, auth, player_color="black")
    assert created["opponent_move"] is None
    assert created["clock"]["active_color"] == "white"
    assert created["clock"]["version"] == 0
    assert scripted_api.contexts == []
    resumed = client.post(
        f"/v1/play-sessions/{created['id']}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["opponent_move"] == "e2e4"
    assert resumed.json()["clock"]["active_color"] == "black"
    assert resumed.json()["clock"]["version"] == 1
    assert created["clock_config"] == {"initial_seconds": "60", "increment_seconds": "2"}
    game = db_session.get(Game, UUID(created["game_id"]))
    assert game.headers["TimeControl"] == "60+2"
    assert game.moves == ["e2e4"]


def test_bot_first_creation_retains_retryable_identity_when_engine_fails(
    client, db_session, frozen_time, scripted_api
):
    auth = register(db_session)

    async def fail() -> None:
        raise RuntimeError("engine unavailable")

    scripted_api.effect = fail
    created = start(client, auth, player_color="black")
    assert created["game_id"] is not None
    assert created["opponent_move"] is None
    assert created["clock"]["version"] == 0
    assert created["clock"]["active_color"] == "white"
    failed = client.post(
        f"/v1/play-sessions/{created['id']}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert failed.status_code == 500
    assert failed.json()["detail"] == "engine unavailable"
    pending = client.get(f"/v1/play-sessions/{created['id']}", headers=auth).json()
    assert pending["clock"]["version"] == 0
    assert pending["clock"]["active_color"] == "white"
    game = db_session.get(Game, UUID(created["game_id"]))
    assert game.moves == []
    scripted_api.effect = None
    scripted_api.move = "e2e4"
    resumed = client.post(
        f"/v1/play-sessions/{created['id']}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["clock"]["version"] == 1
    db_session.refresh(game)
    assert game.moves == ["e2e4"]


@pytest.mark.parametrize(
    "control",
    [
        {"initial_seconds": True},
        {"initial_seconds": "60"},
        {"initial_seconds": 0},
        {"initial_seconds": 86401},
        {"initial_seconds": 60, "increment_seconds": -1},
        {"initial_seconds": 60, "increment_seconds": 61},
        {"initial_seconds": 60, "increment_seconds": 1.5},
        {"initial_seconds": 60, "remaining_ms": 1},
        {"time_remaining_ms": "60000"},
    ],
)
def test_http_rejects_untrusted_clock_inputs(client, db_session, control):
    auth = register(db_session)
    response = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "clock_config": control},
        headers=auth,
    )
    assert response.status_code == 422
    assert db_session.exec(select(PlayClock)).all() == []


def test_http_versions_ownership_and_read_only_pending_state(
    client, db_session, frozen_time, scripted_api
):
    auth = register(db_session)
    created = start(client, auth)
    session_id = created["id"]
    missing = client.post(
        f"/v1/play-sessions/{session_id}/moves", json={"move": "e2e4"}, headers=auth
    )
    assert missing.status_code == 409
    bad_type = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4", "expected_clock_version": True},
        headers=auth,
    )
    assert bad_type.status_code == 422
    other_auth = register(db_session, "bob")
    hidden = client.post(
        f"/v1/play-sessions/{session_id}/opponent-move",
        json={"expected_clock_version": 0},
        headers=other_auth,
    )
    assert hidden.status_code == 404
    wrong_turn = client.post(
        f"/v1/play-sessions/{session_id}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert wrong_turn.status_code == 409
    assert scripted_api.contexts == []
    fetched = client.get(f"/v1/play-sessions/{session_id}", headers=auth)
    assert fetched.status_code == 200
    assert scripted_api.contexts == []
    played = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4", "expected_clock_version": 0},
        headers=auth,
    )
    assert played.status_code == 200, played.text
    assert played.json()["clock"]["version"] == 2
    stale = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "g1f3", "expected_clock_version": 0},
        headers=auth,
    )
    assert stale.status_code == 409


def test_timed_game_is_private_from_later_untimed_continuation(
    client, db_session, frozen_time, scripted_api
):
    auth = register(db_session)
    timed = start(client, auth)
    source_id = UUID(timed["game_id"])
    source = db_session.get(Game, source_id)
    original_pgn = source.pgn
    continuation = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "game_id": str(source_id)},
        headers=auth,
    )
    assert continuation.status_code == 200, continuation.text
    copied = continuation.json()
    assert copied["game_id"] != str(source_id)
    assert copied["clock"] is None
    assert copied["clock_config"] is None
    resigned = client.post(f"/v1/play-sessions/{copied['id']}/resign", headers=auth)
    assert resigned.status_code == 200
    db_session.refresh(source)
    assert source.result == "*"
    assert source.pgn == original_pgn
    saved = client.get(f"/v1/play-sessions/{timed['id']}", headers=auth).json()
    assert saved["status"] == "active"
    assert saved["clock"]["version"] == 0
    assert saved["clock"]["active_color"] == "white"


@pytest.mark.asyncio
async def test_coaching_sees_committed_boundary_and_does_not_pause_clock(
    db_session, frozen_time, monkeypatch
):
    play, game, clock = seed_clock(db_session, frozen_time)
    play.coach_mode = True
    db_session.add(play)
    db_session.commit()
    game_id, session_id = game.id, play.id

    async def prepare(**kwargs):
        assert not db_session.in_transaction()
        with Session(db_session.get_bind()) as other:
            assert other.get(Game, game_id).moves == ["e2e4"]
            assert other.get(PlayClock, session_id).active_color == "black"
        frozen_time.advance(2500)
        return None

    monkeypatch.setattr(play_service, "prepare_coach_interruption", prepare)
    opponent = ScriptedOpponent()
    await PlaySessionService(db_session, opponent).make_move(session_id, "e2e4", 0)
    assert opponent.contexts[0].time_remaining_ms == 57_500
    db_session.refresh(clock)
    assert clock.black_remaining_ms == 59_500


@pytest.mark.asyncio
async def test_bot_board_mate_stops_clock_and_enqueues_once(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("f2f3", "e7e5", "g2g4"))
    service = PlaySessionService(db_session, ScriptedOpponent("d8h4"))
    assert (await service.resume_opponent(play.id, 0)).opponent_move == "d8h4"
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.result == "0-1"
    assert clock.terminal_reason == "board"
    assert clock.active_color is None
    assert clock.black_remaining_ms == 62_000
    assert len(service.pending_analysis) == 1
    with pytest.raises(ClockConflict):
        await service.resume_opponent(play.id, 0)
    assert len(db_session.exec(select(AnalysisJob)).all()) == 1


@pytest.mark.asyncio
async def test_engine_real_work_reduces_pacing_sleep(db_session, frozen_time, monkeypatch):
    play, game, clock = seed_clock(
        db_session, frozen_time, moves=("e2e4",), remaining=3000, increment=0
    )
    slept = []
    monkeypatch.setattr(play_service, "monotonic", lambda: frozen_time.now.timestamp())

    async def engine_work():
        frozen_time.advance(40)

    async def record_sleep(seconds):
        slept.append(seconds)
        frozen_time.advance(round(seconds * 1000))

    monkeypatch.setattr(play_service, "sleep", record_sleep)
    service = PlaySessionService(db_session, ScriptedOpponent(effect=engine_work))
    assert (await service.resume_opponent(play.id, 0)).opponent_move == "e7e5"
    assert slept == pytest.approx([0.060], abs=0.000001)
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4", "e7e5"]
    assert clock.black_remaining_ms == 2900


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remaining", "work_ms", "expected_sleep", "expected_balance"),
    [
        (80, 0, 0.070, 10),
        (80, 40, 0.030, 10),
        (80, 75, None, 5),
        (8, 0, None, 8),
        (3000, 0, 0.100, 2900),
        (3000, 120, None, 2880),
    ],
)
async def test_pacing_leaves_deadline_margin_and_charges_actual_elapsed_time(
    db_session, frozen_time, monkeypatch, remaining, work_ms, expected_sleep, expected_balance
):
    play, game, clock = seed_clock(
        db_session, frozen_time, moves=("e2e4",), remaining=remaining, increment=0
    )
    slept = []
    monkeypatch.setattr(play_service, "monotonic", lambda: frozen_time.now.timestamp())

    async def engine_work():
        frozen_time.advance(work_ms)

    async def record_sleep(seconds):
        slept.append(seconds)
        frozen_time.advance(round(seconds * 1000))

    monkeypatch.setattr(play_service, "sleep", record_sleep)
    opponent = ScriptedOpponent(effect=engine_work)
    result = await PlaySessionService(db_session, opponent).resume_opponent(play.id, 0)
    assert result.opponent_move == "e7e5"
    if expected_sleep is None:
        assert slept == []
    else:
        assert slept == pytest.approx([expected_sleep], abs=0.000001)
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4", "e7e5"]
    assert clock.black_remaining_ms == expected_balance
    assert clock.version == 1
    assert clock.active_color == "white"
    assert clock.terminal_reason is None
    assert len(opponent.contexts) == 1


@pytest.mark.asyncio
async def test_clock_cas_rejects_snapshot_aba_and_preserves_move_boundary(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id

    async def change_generation():
        with Session(db_session.get_bind()) as other:
            saved = other.get(PlaySession, session_id)
            saved.opponent_config = {
                **saved.opponent_config,
                "assistance_generation": "2",
            }
            other.add(saved)
            other.commit()

    service = PlaySessionService(db_session, ScriptedOpponent(effect=change_generation))
    with pytest.raises(play_service.PlaySessionNotActive):
        await service.resume_opponent(session_id, 0)
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert clock.version == 0
    assert clock.active_color == "black"


@pytest.mark.asyncio
async def test_notify_wakes_scheduler_for_new_earlier_deadline(
    db_session, frozen_time, monkeypatch
):
    seed_clock(db_session, frozen_time)
    initial_wait = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop_thread = get_ident()

    def observed_now():
        if get_ident() == loop_thread:
            loop.call_soon(initial_wait.set)
        return frozen_time.now

    monkeypatch.setattr(clocks, "utc_now", observed_now)
    runtime = PlayClockRuntime(db_session.get_bind())
    runtime.start()
    try:
        await asyncio.wait_for(initial_wait.wait(), timeout=2)
        play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",), remaining=100)
        session_id = play.id
        admitted = asyncio.Event()

        async def submit(player_id, job_id, pool):
            admitted.set()

        monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
        frozen_time.advance(100)
        runtime.notify()
        await asyncio.wait_for(admitted.wait(), timeout=1)
        db_session.refresh(clock)
        db_session.refresh(play)
        assert clock.terminal_reason == "flag"
        assert db_session.get(PlaySession, session_id).status == "completed"
    finally:
        await runtime.stop()


def test_timed_continuation_copies_owned_source(client, db_session, frozen_time, scripted_api):
    auth = register(db_session)
    source = Game(
        pgn="source",
        moves=["e2e4", "e7e5"],
        owner_player_id="alice",
        white="Alice",
        black="Original opponent",
        result="1-0",
    )
    db_session.add(source)
    db_session.commit()
    source_id = source.id
    created = start(client, auth, game_id=str(source_id))
    assert created["game_id"] != str(source_id)
    assert created["opponent_config"]["custom_start"] == "true"
    copy = db_session.get(Game, UUID(created["game_id"]))
    assert copy.moves == ["e2e4", "e7e5"]
    assert copy.result == "*"
    assert copy.headers["TimeControl"] == "60+2"
    db_session.refresh(source)
    assert source.result == "1-0"
    assert source.pgn == "source"
    continued = start(client, auth, game_id=created["game_id"], clock_config=None)
    assert continued["game_id"] != created["game_id"]
    ordinary = client.get(f"/v1/games/{continued['game_id']}", headers=auth)
    assert ordinary.status_code == 200
    ordinary_copy = db_session.get(Game, UUID(continued["game_id"]))
    assert ordinary_copy is not None
    assert ordinary_copy.moves == ["e2e4", "e7e5"]
    assert "1. e4 e5 *" in ordinary.json()["pgn"]
    assert "[TimeControl " not in ordinary.json()["pgn"]


@pytest.mark.parametrize("resume_path", ["resume", "opponent-move"])
def test_pending_http_get_does_not_run_engine_but_owned_command_resumes(
    client, db_session, frozen_time, scripted_api, resume_path
):
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    state = client.get(f"/v1/play-sessions/{session_id}", headers=auth)
    assert state.status_code == 200
    assert state.json()["clock"]["active_color"] == "black"
    assert scripted_api.contexts == []
    resumed = client.post(
        f"/v1/play-sessions/{session_id}/{resume_path}",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["opponent_move"] == "e7e5"
    assert resumed.json()["clock"]["version"] == 1
    assert len(scripted_api.contexts) == 1


@pytest.mark.parametrize(
    ("remaining", "increment", "target"),
    [(10, 0, 10), (3000, 0, 100), (9000, 0, 300), (30_000, 2, 1500), (90_000, 0, 1500)],
)
def test_pacing_formula_is_bounded_by_live_balance(remaining, increment, target):
    assert clocks.thinking_target_ms(remaining, increment) == target


@pytest_asyncio.fixture
async def timed_assistance_api(db_session, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:
    # Use real request Sessions and the production grader/pool without a running
    # scheduler, so expiry during grading is adjudicated deterministically.
    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=2)
    monkeypatch.setattr(app.state, "engine_pool_manager", manager, raising=False)
    monkeypatch.setattr(app.state, "play_clock_runtime", None, raising=False)
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["candidates", "guard"])
async def test_timed_grading_elapsed_time_is_charged_and_generation_still_fences_moves(
    timed_assistance_api, db_session, frozen_time, monkeypatch, mode
):
    auth = register(db_session)
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id, game_id = play.id, game.id
    initial_deadline = clocks.as_utc(clock.deadline_at)
    db_session.rollback()
    searches: list[chess.Board] = []

    async def analyze(board, **_kwargs):
        searches.append(board.copy())
        frozen_time.advance(1250)
        return EngineAnalysis(
            raw_result=[{"score_cp": 0, "pv": [next(iter(board.legal_moves)).uci()]}]
        )

    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", AsyncMock(side_effect=analyze))
    graded = await timed_assistance_api.post(
        f"/v1/play-sessions/{session_id}/assistance",
        headers=auth,
        json={
            "player_id": "alice",
            "fen": chess.STARTING_FEN,
            "moves": ["e2e4"],
            "mode": mode,
        },
    )
    assert graded.status_code == 200, graded.text
    assert graded.json()["generation"] == 1
    assert graded.json()["assistance_modes"] == [mode]
    assert graded.json()["grades"][0]["band"] == "sound"
    assert [len(board.move_stack) for board in searches] == [0, 1]
    with Session(db_session.get_bind()) as reader:
        saved = reader.get(PlayClock, session_id)
        assert saved.version == 0
        assert clocks.as_utc(saved.deadline_at) == initial_deadline
        assert project_clock(saved).white_remaining_ms == 57_500
        reveal = reader.exec(select(assistance.SessionHintReveal)).one()
        assert reveal.candidate_used is (mode == "candidates")
        assert reveal.guard_used is (mode == "guard")
        assert reveal.effective_step == 3

    opponent = ScriptedOpponent()
    monkeypatch.setitem(app.dependency_overrides, play_api.get_opponent_provider, lambda: opponent)
    rejected = await timed_assistance_api.post(
        f"/v1/play-sessions/{session_id}/moves",
        headers=auth,
        json={"move": "e2e4", "expected_clock_version": 0, "expected_generation": 0},
    )
    assert rejected.status_code == 409
    assert opponent.contexts == []
    with Session(db_session.get_bind()) as reader:
        assert reader.get(Game, game_id).moves == []
        assert reader.get(PlayClock, session_id).version == 0

    moved = await timed_assistance_api.post(
        f"/v1/play-sessions/{session_id}/moves",
        headers=auth,
        json={
            "move": "e2e4",
            "expected_clock_version": 0,
            "expected_generation": graded.json()["generation"],
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["clock"]["version"] == 2
    assert moved.json()["clock"]["white_remaining_ms"] == 59_500
    assert moved.json()["clock"]["black_remaining_ms"] == 62_000
    with Session(db_session.get_bind()) as reader:
        assert reader.get(Game, game_id).moves == ["e2e4", "e7e5"]
        assert assistance.assistance_modes(reader, session_id) == [mode]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["candidates", "guard"])
@pytest.mark.parametrize("flag_during_grade", [False, True])
async def test_timed_grading_cannot_disclose_or_override_elapsed_flagfall(
    timed_assistance_api, db_session, frozen_time, monkeypatch, mode, flag_during_grade
):
    auth = register(db_session)
    play, game, _ = seed_clock(db_session, frozen_time)
    session_id, game_id = play.id, game.id
    db_session.rollback()
    searches: list[chess.Board] = []

    async def analyze(board, **_kwargs):
        searches.append(board.copy())
        frozen_time.advance(30_000)
        if len(searches) == 2 and flag_during_grade:
            with Session(db_session.get_bind()) as writer:
                PlaySessionService(writer, ScriptedOpponent()).expire_clock(session_id)
        return EngineAnalysis(
            raw_result=[{"score_cp": 0, "pv": [next(iter(board.legal_moves)).uci()]}]
        )

    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", AsyncMock(side_effect=analyze))
    graded = await timed_assistance_api.post(
        f"/v1/play-sessions/{session_id}/assistance",
        headers=auth,
        json={
            "player_id": "alice",
            "fen": chess.STARTING_FEN,
            "moves": ["e2e4"],
            "mode": mode,
        },
    )
    assert graded.status_code == 409, graded.text
    assert set(graded.json()) == {"detail"}
    assert [len(board.move_stack) for board in searches] == [0, 1]
    with Session(db_session.get_bind()) as reader:
        assert reader.exec(select(assistance.SessionHintReveal)).all() == []
        assert reader.get(Game, game_id).moves == []
        saved_play = reader.get(PlaySession, session_id)
        saved_clock = reader.get(PlayClock, session_id)
        assert saved_clock.version == int(flag_during_grade)
        assert saved_play.opponent_config["assistance_generation"] == str(int(flag_during_grade))
        if not flag_during_grade:
            assert saved_play.status == "active"
            assert project_clock(saved_clock).white_remaining_ms == 0
            assert saved_clock.terminal_reason is None

    # An unchanged expected clock/generation cannot waive the elapsed deadline.
    opponent_calls = []

    async def unexpected_opponent(*args, **kwargs):
        opponent_calls.append(args)
        raise AssertionError("An elapsed human turn must not start an opponent")

    monkeypatch.setattr(PlaySessionService, "play_opponent_move", unexpected_opponent)
    attempted = await timed_assistance_api.post(
        f"/v1/play-sessions/{session_id}/moves",
        headers=auth,
        json={"move": "e2e4", "expected_clock_version": 0, "expected_generation": 0},
    )
    assert attempted.status_code == 409
    assert opponent_calls == []
    repeated = await timed_assistance_api.post(
        f"/v1/play-sessions/{session_id}/assistance",
        headers=auth,
        json={
            "player_id": "alice",
            "fen": chess.STARTING_FEN,
            "moves": ["e2e4"],
            "mode": mode,
        },
    )
    assert repeated.status_code == 409
    assert len(searches) == 2
    with Session(db_session.get_bind()) as reader:
        saved_play = reader.get(PlaySession, session_id)
        saved_clock = reader.get(PlayClock, session_id)
        saved_game = reader.get(Game, game_id)
        assert saved_play.status == "completed"
        assert saved_clock.version == 1
        assert saved_clock.terminal_reason == "flag"
        assert saved_clock.flagged_color == "white"
        assert saved_clock.active_color is None
        assert saved_clock.deadline_at is None
        assert saved_game.moves == []
        assert saved_game.result == "0-1"
        assert reader.exec(select(assistance.SessionHintReveal)).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["candidates", "guard"])
async def test_overdue_timed_assistance_rejects_before_engine_or_disclosure(
    timed_assistance_api, db_session, frozen_time, monkeypatch, mode
):
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time)
    session_id = play.id
    db_session.rollback()
    frozen_time.advance(60_000)
    analyze = AsyncMock()
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    response = await timed_assistance_api.post(
        f"/v1/play-sessions/{session_id}/assistance",
        headers=auth,
        json={
            "player_id": "alice",
            "fen": chess.STARTING_FEN,
            "moves": ["e2e4"],
            "mode": mode,
        },
    )
    assert response.status_code == 409, response.text
    analyze.assert_not_awaited()
    with Session(db_session.get_bind()) as reader:
        assert reader.exec(select(assistance.SessionHintReveal)).all() == []
        assert reader.get(PlayClock, session_id).version == 0
