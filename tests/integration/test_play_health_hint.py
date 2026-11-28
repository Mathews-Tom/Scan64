"""Health meters during play count as a hint only when the learner asks (#417)."""

from __future__ import annotations

import chess
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.chess.games.assistance import SessionHintReveal
from tests.integration.test_play_assistance import OWNER, _register, _source


def test_a_counted_reading_is_the_first_hint_level(client: TestClient, db_session: Session) -> None:
    headers = _register(db_session)
    play, _, board = _source(db_session)
    response = client.post(
        f"/v1/play-sessions/{play.id}/health-hint",
        headers=headers,
        json={"player_id": OWNER, "fen": board.fen()},
    )
    assert response.status_code == 200
    assert response.json()["assistance_modes"] == ["hint"]
    reveal = db_session.exec(select(SessionHintReveal)).one()
    assert (reveal.highest_step, reveal.candidate_used, reveal.guard_used) == (1, False, False)


def test_a_stale_or_foreign_position_records_nothing(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session)
    other = _register(db_session, "health-hint-other")
    play, _, _ = _source(db_session)
    after_e4 = chess.Board()
    after_e4.push_uci("e2e4")
    stale = client.post(
        f"/v1/play-sessions/{play.id}/health-hint",
        headers=headers,
        json={"player_id": OWNER, "fen": after_e4.fen()},
    )
    foreign = client.post(
        f"/v1/play-sessions/{play.id}/health-hint",
        headers=other,
        json={"player_id": OWNER, "fen": chess.STARTING_FEN},
    )
    assert stale.status_code == 409
    assert foreign.status_code == 404
    assert db_session.exec(select(SessionHintReveal)).all() == []
