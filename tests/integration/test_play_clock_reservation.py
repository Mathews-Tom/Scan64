from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import pytest
from sqlmodel import Session, select

import scan64.chess.analysis.coach_interruption as coach_interruption
from scan64.api.models import Player
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import ClockConflict, PlayClockRuntime, as_utc
from scan64.chess.games.models import Game
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.content.models import StudySession
from scan64.learning.plugins.host_registry import clear_host_registry, initialize_host_registry
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager
from tests.integration.test_coach_interruption_attempts import COACH_FEN, _analysis_for
from tests.integration.test_play_clocks import (
    FrozenTime,
    ScriptedOpponent,
    seed_clock,
)
from tests.integration.test_play_clocks import (
    frozen_time as frozen_time,
)


class PausedCoachPool(EnginePoolManager):
    def __init__(self) -> None:
        super().__init__(StockfishConfig())
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []

    async def analyze_interactive(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        multipv: int = 1,
        time_ms: int | None = None,
    ) -> EngineAnalysis:
        self.calls.append(fen)
        if len(self.calls) == 1:
            self.entered.set()
            await self.release.wait()
        return _analysis_for(fen)


@pytest.fixture(autouse=True)
def deterministic_coach(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("SCAN64_LLM_CONFIG", raising=False)
    monkeypatch.setattr(coach_interruption, "COACH_PREPARATION_TIMEOUT_SECONDS", 30.0)
    initialize_host_registry()
    try:
        yield
    finally:
        clear_host_registry()


def seed_coached_turn(db: Session, frozen: FrozenTime) -> tuple[UUID, UUID]:
    db.add(Player(id="alice"))
    db.commit()
    play, game, _ = seed_clock(db, frozen, fen=COACH_FEN)
    play.coach_mode = True
    db.add(play)
    db.commit()
    session_id, game_id = play.id, game.id
    db.rollback()
    return session_id, game_id


@pytest.mark.asyncio
async def test_coach_preparation_cannot_be_overtaken_by_another_owned_resume(
    db_session: Session, frozen_time: FrozenTime
) -> None:
    session_id, game_id = seed_coached_turn(db_session, frozen_time)
    runtime = PlayClockRuntime(db_session.get_bind())
    pool = PausedCoachPool()
    opponent = ScriptedOpponent("e8d7")
    service = PlaySessionService(
        db_session, opponent, engine_pool_manager=pool, clock_runtime=runtime
    )
    frozen_time.advance(1000)
    task = asyncio.create_task(service.make_move(session_id, "e2e3", 0))
    try:
        # Pause inside the real diagnostic, after the human boundary is durable.
        await asyncio.wait_for(pool.entered.wait(), timeout=5)
        assert not db_session.in_transaction()
        with Session(db_session.get_bind()) as other:
            duplicate = PlaySessionService(other, opponent, clock_runtime=runtime)
            with pytest.raises(ClockConflict, match="already in progress"):
                await duplicate.resume_opponent(session_id, 1)
            game = other.get(Game, game_id)
            clock = other.get(PlayClock, session_id)
            assert game is not None and clock is not None
            assert game.moves == ["e2e3"]
            assert clock.version == 1
            assert clock.active_color == "black"
            assert clock.deadline_at is not None
            assert clock.white_remaining_ms == 61_000
            assert as_utc(clock.deadline_at) == frozen_time.now + timedelta(seconds=60)
            assert other.exec(select(PersistedLessonOpportunity)).all() == []
        assert opponent.contexts == []
        frozen_time.advance(2500)
        pool.release.set()
        result = await asyncio.wait_for(task, timeout=5)
        assert result.opponent_move == "e8d7"
        assert result.interruption is not None
        assert len(pool.calls) == 2
        assert len(opponent.contexts) == 1
        assert opponent.contexts[0].time_remaining_ms == 57_500
        with Session(db_session.get_bind()) as reader:
            game = reader.get(Game, game_id)
            clock = reader.get(PlayClock, session_id)
            assert game is not None and clock is not None
            assert game.moves == ["e2e3", "e8d7"]
            assert clock.version == 2
            assert clock.white_remaining_ms == 61_000
            assert clock.black_remaining_ms == 59_500
            assert clock.active_color == "white"
            assert clock.deadline_at is not None
            assert as_utc(clock.deadline_at) == frozen_time.now + timedelta(milliseconds=61_000)
            opportunity = reader.exec(select(PersistedLessonOpportunity)).one()
            study = reader.exec(select(StudySession)).one()
            assert opportunity.id == result.interruption.opportunity_id
            assert opportunity.game_id == game_id
            assert opportunity.verification_status == "verified"
            assert study.id == result.interruption.study_session_id
            assert study.domain == f"coach_interruption:{game_id}"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.stop()
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_mode", ["cancel_coaching", "cancel_engine", "engine_failure"])
async def test_interrupted_coached_turn_releases_reservation_for_another_owner_service(
    db_session: Session, frozen_time: FrozenTime, exit_mode: str
) -> None:
    session_id, game_id = seed_coached_turn(db_session, frozen_time)
    runtime = PlayClockRuntime(db_session.get_bind())
    pool = PausedCoachPool()
    engine_entered = asyncio.Event()

    async def interrupt_engine() -> None:
        engine_entered.set()
        if exit_mode == "engine_failure":
            raise RuntimeError("engine unavailable")
        await asyncio.Event().wait()

    opponent = ScriptedOpponent("e8d7", effect=interrupt_engine)
    service = PlaySessionService(
        db_session, opponent, engine_pool_manager=pool, clock_runtime=runtime
    )
    frozen_time.advance(1000)
    task = asyncio.create_task(service.make_move(session_id, "e2e3", 0))
    try:
        await asyncio.wait_for(pool.entered.wait(), timeout=5)
        frozen_time.advance(2500)
        if exit_mode != "cancel_coaching":
            pool.release.set()
            await asyncio.wait_for(engine_entered.wait(), timeout=5)
        if exit_mode == "engine_failure":
            with pytest.raises(RuntimeError, match="engine unavailable"):
                await asyncio.wait_for(task, timeout=5)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(opponent.contexts) == (0 if exit_mode == "cancel_coaching" else 1)
        frozen_time.advance(1000)
        recovered_opponent = ScriptedOpponent("e8d7")
        with Session(db_session.get_bind()) as other:
            game = other.get(Game, game_id)
            clock = other.get(PlayClock, session_id)
            assert game is not None and clock is not None
            assert game.moves == ["e2e3"]
            assert clock.version == 1
            assert clock.white_remaining_ms == 61_000
            assert clock.black_remaining_ms == 60_000
            assert clock.active_color == "black"
            assert clock.deadline_at is not None
            assert as_utc(clock.deadline_at) == frozen_time.now + timedelta(milliseconds=56_500)
            recovery = PlaySessionService(other, recovered_opponent, clock_runtime=runtime)
            assert await recovery.resume_opponent(session_id, 1) == "e8d7"
            other.refresh(game)
            other.refresh(clock)
            assert game.moves == ["e2e3", "e8d7"]
            assert clock.version == 2
            assert clock.white_remaining_ms == 61_000
            assert clock.black_remaining_ms == 58_500
            assert clock.active_color == "white"
            assert clock.deadline_at is not None
            assert as_utc(clock.deadline_at) == frozen_time.now + timedelta(milliseconds=61_000)
        assert len(recovered_opponent.contexts) == 1
        assert recovered_opponent.contexts[0].time_remaining_ms == 56_500
        # The original service's local guard must also have been released.
        with pytest.raises(ClockConflict, match="Expected clock version"):
            await service.resume_opponent(session_id, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.stop()
        await pool.close()
