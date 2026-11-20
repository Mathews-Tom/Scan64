from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.api.play as play_api
from scan64.api.app import app
from scan64.chess.analysis.models import AnalysisJob, PersistedLessonOpportunity
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from tests.integration.test_coach_interruption_attempts import (
    CHECKMATE_FEN,
    COACH_FEN,
    _install_deterministic_diagnostic,
)
from tests.integration.test_play_clocks import FrozenTime, ScriptedOpponent, register, seed_clock
from tests.integration.test_play_clocks import frozen_time as frozen_time


def _seed_play(
    db: Session,
    frozen: FrozenTime,
    *,
    timed: bool,
    fen: str | None = None,
    moves: tuple[str, ...] = (),
) -> tuple[UUID, UUID]:
    play, game, clock = seed_clock(db, frozen, fen=fen, moves=moves)
    if not timed:
        db.delete(clock)
        play.clock_config = None
        db.add(play)
        db.commit()
    session_id, game_id = play.id, game.id
    db.rollback()
    return session_id, game_id


@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize("coach_mode", [False, True])
def test_move_response_keeps_owned_commit_when_resignation_commits_before_assembly(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    timed: bool,
    coach_mode: bool,
) -> None:
    auth = register(db_session)
    session_id, game_id = _seed_play(db_session, frozen_time, timed=timed, fen=COACH_FEN)
    play = db_session.get(PlaySession, session_id)
    assert play is not None
    play.coach_mode = coach_mode
    db_session.add(play)
    db_session.commit()
    opponent = ScriptedOpponent("e8d7")
    monkeypatch.setitem(app.dependency_overrides, play_api.get_opponent_provider, lambda: opponent)
    monkeypatch.delenv("SCAN64_LLM_CONFIG", raising=False)
    if coach_mode:
        _install_deterministic_diagnostic(monkeypatch)

    original_commit = Session.commit
    owned_commits = 0
    competing_commits = 0
    move_commit = 2 if timed else 1
    committed_at = frozen_time.now

    def commit_then_resign(db: Session) -> None:
        nonlocal owned_commits, competing_commits
        if db is db_session:
            owned_commits += 1
        original_commit(db)
        if db is not db_session or owned_commits != move_commit:
            return
        # This is after the actual move COMMIT, before its service call returns.
        # A fresh Session proves a distinct committed terminal transition wins
        # the next version; the response must not splice that state into the move.
        frozen_time.advance(1000)
        with Session(db_session.get_bind()) as other:
            before = other.get(Game, game_id)
            assert before is not None and before.moves == ["e2e3", "e8d7"]
            PlaySessionService(other, opponent).resign(session_id)
            competing_commits += 1

    monkeypatch.setattr(Session, "commit", commit_then_resign)
    response = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        headers=auth,
        json={"move": "e2e3", **({"expected_clock_version": 0} if timed else {})},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["opponent_move"] == "e8d7"
    assert data["status"] == "active"
    assert competing_commits == 1
    assert owned_commits == move_commit + int(timed and coach_mode)
    if timed:
        assert data["clock"]["version"] == 2
        assert data["clock"]["active_color"] == "white"
        assert data["clock"]["terminal_reason"] is None
        assert data["clock"]["white_remaining_ms"] == 62_000
        assert data["clock"]["black_remaining_ms"] == 62_000
        assert data["clock"]["server_now"] == committed_at.isoformat().replace("+00:00", "Z")
    else:
        assert data["clock"] is None
    assert (data["critical_interruption"] is not None) is coach_mode

    with Session(db_session.get_bind()) as reader:
        saved = reader.get(PlaySession, session_id)
        game = reader.get(Game, game_id)
        assert saved is not None and saved.status == "completed"
        assert game is not None and game.moves == ["e2e3", "e8d7"]
        assert game.result == "0-1"
        clock = reader.get(PlayClock, session_id)
        if timed:
            assert clock is not None
            assert clock.version == 3
            assert clock.active_color is None
            assert clock.terminal_reason == "resignation"
            assert clock.white_remaining_ms == 61_000
        else:
            assert clock is None
        assert len(reader.exec(select(AnalysisJob)).all()) == 1
        opportunities = reader.exec(select(PersistedLessonOpportunity)).all()
        assert len(opportunities) == int(coach_mode)
        if coach_mode:
            assert str(opportunities[0].id) == data["critical_interruption"]["opportunity_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize("human_mate", [False, True])
async def test_board_terminal_move_returns_its_committed_snapshot(
    db_session: Session,
    frozen_time: FrozenTime,
    timed: bool,
    human_mate: bool,
) -> None:
    register(db_session)
    session_id, game_id = _seed_play(
        db_session,
        frozen_time,
        timed=timed,
        fen=CHECKMATE_FEN if human_mate else None,
        moves=() if human_mate else ("f2f3", "e7e5"),
    )
    opponent = ScriptedOpponent("d8h4")
    service = PlaySessionService(db_session, opponent)
    result = await service.make_move(
        session_id, "g6g7" if human_mate else "g2g4", 0 if timed else None
    )
    # Neither expiration nor subsequent local ORM mutation can rewrite the
    # result's status/clock. The terminal commit still creates one analysis job.
    db_session.expire_all()
    play = db_session.get(PlaySession, session_id)
    clock = db_session.get(PlayClock, session_id)
    game = db_session.get(Game, game_id)
    assert play is not None and play.status == "completed"
    assert game is not None and game.result == ("1-0" if human_mate else "0-1")
    assert result.status == "completed"
    assert result.opponent_move == (None if human_mate else "d8h4")
    assert result.interruption is None
    assert len(opponent.contexts) == int(not human_mate)
    assert len(db_session.exec(select(AnalysisJob)).all()) == 1
    assert len(service.pending_analysis) == 1
    if timed:
        assert clock is not None and result.clock is not None
        assert result.clock.version == (1 if human_mate else 2)
        assert result.clock.active_color is None
        assert result.clock.deadline_at is None
        assert result.clock.terminal_reason == "board"
        assert result.clock.flagged_color is None
        clock.version += 1
        clock.terminal_reason = "resignation"
        assert result.clock.version == (1 if human_mate else 2)
        assert result.clock.terminal_reason == "board"
    else:
        assert clock is None and result.clock is None
    play.status = "active"
    assert result.status == "completed"
    db_session.rollback()
