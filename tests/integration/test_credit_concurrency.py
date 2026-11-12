"""Interleavings at the engine/write and board/snapshot boundaries."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.api.insights as insights
import scan64.chess.analysis.jobs as jobs
from scan64.api.app import app
from scan64.chess.analysis.credit_models import PositiveMoveCredit
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import FastPassResult
from scan64.chess.games.assistance import SessionHintReveal
from scan64.chess.games.models import Game, PlaySession
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.plugins.registry import PluginRegistry
from scan64.learning.profiling.models import SkillState
from tests.integration.test_positive_move_credit import _stage
from tests.integration.test_session_hint_tracking import _register


@pytest.mark.asyncio
async def test_takeback_then_resign_during_analysis_cannot_award_removed_move(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    game, play, _, _ = _stage(db_session)
    game.result = "*"
    play.status = "active"
    db_session.add_all([game, play])
    db_session.commit()
    analyzed_length = len(game.moves)

    async def takeback_then_resign(*args: object, **kwargs: object) -> FastPassResult:
        with Session(db_session.get_bind()) as concurrent:
            changed = concurrent.get(Game, game.id)
            completed = concurrent.get(PlaySession, play.id)
            assert changed is not None and completed is not None
            changed.moves = changed.moves[:-2]
            changed.result = "0-1"
            completed.status = "completed"
            concurrent.add_all([changed, completed])
            concurrent.commit()
        return FastPassResult(candidates=[], evaluations=[
            EngineAnalysis(raw_result=[{"score_cp": 0, "pv": []}])
            for _ in range(analyzed_length + 1)
        ])

    monkeypatch.setattr(jobs.FastPassOrchestrator, "run_fast_pass", takeback_then_resign)
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    with pytest.raises(ValueError, match="Game changed during analysis"):
        await jobs.run_analysis_for_game(game, db_session, registry=registry)
    assert db_session.exec(select(PositiveMoveCredit)).all() == []
    assert db_session.exec(select(SkillState)).all() == []
    db_session.refresh(play)
    assert play.credit_analysis_status is None


def test_takeback_between_board_loading_and_engine_work_returns_conflict(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = _register(db_session)
    game = Game(pgn="*", moves=["e2e4", "e7e5"], owner_player_id="hint-tracking-owner")
    db_session.add(game)
    db_session.flush()
    play = PlaySession(
        player_id="hint-tracking-owner", game_id=game.id,
        assistance_tracking_version=1, opponent_config={"player_color": "white"},
    )
    db_session.add(play)
    db_session.commit()
    original = insights._position

    def takeback_after_loading(
        body: insights.PositionInsightRequest, session: Session,
    ) -> object:
        source = original(body, session)
        with Session(db_session.get_bind()) as concurrent:
            changed = concurrent.get(Game, game.id)
            current = concurrent.get(PlaySession, play.id)
            assert changed is not None and current is not None
            changed.moves = []
            current.opponent_config = {
                **current.opponent_config, "assistance_generation": "1",
            }
            concurrent.add_all([changed, current])
            concurrent.commit()
        return source

    monkeypatch.setattr(insights, "_position", takeback_after_loading)
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint",
        AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["g1f3"], "score_cp": 0}])),
    )
    response = client.post(
        "/v1/insights/position", headers=headers,
        json={"player_id": play.player_id, "play_session_id": str(play.id), "step": 3},
    )
    assert response.status_code == 409
    assert "arrows" not in response.json()
    assert db_session.exec(select(SessionHintReveal)).all() == []
