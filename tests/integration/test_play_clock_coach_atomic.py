from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.models import DeletionAudit
from scan64.chess.analysis import coach_interruption
from scan64.chess.analysis.coach_interruption import CoachInterruption, PreparedCoachInterruption
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games import play_session_service
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import ClockConflict, as_utc
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.content.models import StudySession
from scan64.explanations.settings import OPERATOR_LLM_CONFIG, OwnerExplanationSettings
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.providers.llm import local_ollama
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager
from tests.integration.test_coach_interruption_attempts import (
    COACH_FEN,
    _coach_session,
    _install_deterministic_diagnostic,
)
from tests.integration.test_play_clocks import (
    FrozenTime,
    ScriptedOpponent,
    register,
    seed_clock,
)
from tests.integration.test_play_clocks import frozen_time as frozen_time


@pytest.mark.asyncio
@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize("generation_changed", [False, True])
async def test_coach_uses_only_the_move_snapshot_owners_consent(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    timed: bool,
    generation_changed: bool,
) -> None:
    register(db_session)
    if timed:
        play, _, _ = seed_clock(db_session, frozen_time, fen=COACH_FEN)
        play.coach_mode = True
        db_session.add(play)
    else:
        play = _coach_session(db_session, coach_mode=True)
    db_session.add(OwnerExplanationSettings(player_id="alice", model="test-local-model"))
    db_session.commit()
    monkeypatch.delenv(OPERATOR_LLM_CONFIG, raising=False)
    _install_deterministic_diagnostic(monkeypatch)
    prepare = coach_interruption.prepare_coach_interruption
    bind = db_session.get_bind()

    async def prepare_after_snapshot(**kwargs: object) -> PreparedCoachInterruption | None:
        if generation_changed:
            with Session(bind) as writer:
                writer.add(DeletionAudit(id="coach-epoch", player_id="alice"))
                writer.commit()
        return await prepare(**kwargs)  # type: ignore[arg-type]

    requests: list[str] = []

    async def unavailable(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(503)

    monkeypatch.setattr(play_session_service, "prepare_coach_interruption", prepare_after_snapshot)
    monkeypatch.setattr(
        local_ollama.httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(unavailable)
    )
    pool = EnginePoolManager(StockfishConfig())
    service = PlaySessionService(db_session, ScriptedOpponent(), engine_pool_manager=pool)
    try:
        result = await service.make_move(play.id, "e2e3", 0 if timed else None)
    finally:
        await pool.close()

    assert result.opponent_move == "e8d7"
    assert result.interruption is not None
    assert result.interruption.lesson.explanation.text
    assert requests == ([] if generation_changed else ["/api/tags"])


@pytest.mark.asyncio
async def test_failed_coach_write_preserves_only_the_durable_human_turn(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register(db_session)
    play, game, _ = seed_clock(db_session, frozen_time, fen=COACH_FEN)
    play.coach_mode = True
    db_session.add(play)
    db_session.commit()
    session_id, game_id = play.id, game.id
    _install_deterministic_diagnostic(monkeypatch)
    pool = EnginePoolManager(StockfishConfig())
    service = PlaySessionService(db_session, ScriptedOpponent(), engine_pool_manager=pool)
    original_add = PreparedCoachInterruption.add_to
    staged = []

    def stage_then_fail(prepared, session):
        staged.append(original_add(prepared, session))
        session.flush()
        raise RuntimeError("injected interruption write failure")

    try:
        with monkeypatch.context() as fault:
            fault.setattr(PreparedCoachInterruption, "add_to", stage_then_fail)
            with pytest.raises(RuntimeError, match="injected interruption write failure"):
                await service.make_move(session_id, "e2e3", 0)
        assert len(staged) == 1
        assert staged[0] is not None
        with Session(db_session.get_bind()) as reader:
            saved_game = reader.get(Game, game_id)
            saved_play = reader.get(PlaySession, session_id)
            saved_clock = reader.get(PlayClock, session_id)
            assert saved_game is not None and saved_game.moves == ["e2e3"]
            assert saved_game.result == "*"
            assert saved_play is not None and saved_play.status == "active"
            assert saved_clock is not None and saved_clock.version == 1
            assert saved_clock.active_color == "black"
            assert reader.exec(select(PersistedLessonOpportunity)).all() == []

        # Recover the pending bot turn without submitting the human move again.
        recovered = await service.resume_opponent(session_id, 1)
        assert recovered.opponent_move == "e8d7"
        assert recovered.clock is not None and recovered.clock.version == 2
        with Session(db_session.get_bind()) as reader:
            saved_game = reader.get(Game, game_id)
            assert saved_game is not None and saved_game.moves == ["e2e3", "e8d7"]
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_ms", [200, 1_000], ids=["human-clock-starts-after", "bot-flags"])
async def test_coach_staging_is_charged_to_the_bot_turn(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    stage_ms: int,
) -> None:
    register(db_session)
    play, game, clock = seed_clock(
        db_session, frozen_time, fen=COACH_FEN, increment=0, remaining=100
    )
    play.coach_mode = True
    clock.black_remaining_ms = 1_000
    db_session.add(play)
    db_session.add(clock)
    db_session.commit()
    session_id, game_id = play.id, game.id
    bot_started = frozen_time.now
    _install_deterministic_diagnostic(monkeypatch)
    pool = EnginePoolManager(StockfishConfig())
    service = PlaySessionService(db_session, ScriptedOpponent(), engine_pool_manager=pool)
    original_add = PreparedCoachInterruption.add_to
    staged: list[CoachInterruption | None] = []

    def stage_then_advance(
        prepared: PreparedCoachInterruption, session: Session
    ) -> CoachInterruption | None:
        staged.append(original_add(prepared, session))
        session.flush()
        staged_game = session.get(Game, game_id)
        staged_clock = session.get(PlayClock, session_id)
        assert staged_game is not None and staged_game.moves == ["e2e3", "e8d7"]
        assert staged_clock is not None and staged_clock.active_color == "black"
        assert staged_clock.version == 1
        assert len(session.exec(select(PersistedLessonOpportunity)).all()) == 1
        frozen_time.advance(stage_ms)
        return staged[-1]

    monkeypatch.setattr(PreparedCoachInterruption, "add_to", stage_then_advance)
    try:
        if stage_ms == 1_000:
            with pytest.raises(ClockConflict, match="deadline"):
                await service.make_move(session_id, "e2e3", 0)
        else:
            result = await service.make_move(session_id, "e2e3", 0)
            assert result.opponent_move == "e8d7"
            assert result.interruption is staged[0]
            assert result.status == "active"
            assert result.clock is not None and result.clock.version == 2
            assert result.clock.active_color == "white"
            assert result.clock.white_remaining_ms == 100
            assert result.clock.black_remaining_ms == 1_000 - stage_ms
            assert result.clock.deadline_at == frozen_time.now + timedelta(milliseconds=100)

        assert len(staged) == 1 and staged[0] is not None
        with Session(db_session.get_bind()) as reader:
            saved_game = reader.get(Game, game_id)
            saved_play = reader.get(PlaySession, session_id)
            saved_clock = reader.get(PlayClock, session_id)
            assert saved_play is not None and saved_play.status == "active"
            assert saved_game is not None and saved_game.result == "*"
            assert saved_clock is not None
            assert saved_clock.deadline_at is not None
            if stage_ms == 1_000:
                assert saved_game.moves == ["e2e3"]
                assert saved_play.opponent_config["assistance_generation"] == "1"
                assert saved_clock.version == 1
                assert saved_clock.active_color == "black"
                assert as_utc(saved_clock.deadline_at) == bot_started + timedelta(
                    milliseconds=1_000
                )
                assert saved_clock.white_remaining_ms == 100
                assert saved_clock.black_remaining_ms == 1_000
                assert reader.exec(select(PersistedLessonOpportunity)).all() == []
                assert reader.exec(select(EngineAnalysis)).all() == []
                assert reader.exec(select(Evidence)).all() == []
                assert reader.exec(select(StudySession)).all() == []
                assert reader.exec(select(ReviewSchedule)).all() == []
                assert reader.exec(select(ProfileObservation)).all() == []
                assert reader.exec(select(SkillState)).all() == []
            else:
                assert saved_game.moves == ["e2e3", "e8d7"]
                assert saved_play.opponent_config["assistance_generation"] == "2"
                assert saved_clock.version == 2
                assert saved_clock.active_color == "white"
                assert as_utc(saved_clock.deadline_at) == frozen_time.now + timedelta(
                    milliseconds=100
                )
                assert len(reader.exec(select(PersistedLessonOpportunity)).all()) == 1
    finally:
        await pool.close()
