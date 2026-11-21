from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.chess.analysis.coach_interruption import PreparedCoachInterruption
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager
from tests.integration.test_coach_interruption_attempts import (
    COACH_FEN,
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
