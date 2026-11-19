"""A disclosed verdict authorizes only its exact durable Play generation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.app import app
from scan64.chess.games.assistance import PlaySnapshot, claim_snapshot
from scan64.chess.games.models import Game, PlaySession
from tests.integration.test_play_assistance import OWNER, _register, _request, _source, _witness

MATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"


@pytest.mark.parametrize("mode", ["candidates", "guard"])
def test_superseded_verdict_cannot_commit_but_fresh_choice_plays_exact_move(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    headers = _register(db_session)
    play, game, board = _source(db_session, fen=MATE_FEN)
    # Legacy owned sessions may have owner-less games; avoid unrelated analysis jobs.
    game.owner_player_id = None
    db_session.add(game)
    db_session.commit()
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint", AsyncMock(side_effect=_witness)
    )
    first = _request(client, play, headers, fen=board.fen(), moves=["f7g7"], mode=mode)
    assert first.status_code == 200, first.text
    other_mode = "guard" if mode == "candidates" else "candidates"
    fresh = _request(client, play, headers, fen=board.fen(), moves=["f7g7"], mode=other_mode)
    assert fresh.status_code == 200, fresh.text
    stale = client.post(
        f"/v1/play-sessions/{play.id}/moves",
        headers=headers,
        json={"move": "f7g7", "expected_generation": first.json()["generation"]},
    )
    assert stale.status_code == 409
    db_session.expire_all()
    stored_game = db_session.get(Game, game.id)
    stored_play = db_session.get(PlaySession, play.id)
    assert stored_game is not None and stored_game.moves == []
    assert stored_play is not None and stored_play.status == "active"
    committed = client.post(
        f"/v1/play-sessions/{play.id}/moves",
        headers=headers,
        json={"move": "f7g7", "expected_generation": fresh.json()["generation"]},
    )
    assert committed.status_code == 200, committed.text
    db_session.expire_all()
    stored = db_session.get(Game, game.id)
    assert stored is not None and stored.moves == ["f7g7"] and stored.result == "1-0"
    assert "Qg7#" in stored.pgn
    resumed = client.get(f"/v1/play-sessions/{play.id}", headers=headers)
    assert resumed.json()["status"] == "completed"
    assert resumed.json()["assistance_modes"] == ["candidates", "guard"]


def test_same_position_after_aba_rejects_old_verdict_without_erasing_assistance(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _register(db_session)
    play, game, board = _source(db_session, fen=MATE_FEN)
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint", AsyncMock(side_effect=_witness)
    )
    verdict = _request(client, play, headers, fen=board.fen(), moves=["f7g7"], mode="guard")
    assert verdict.status_code == 200, verdict.text
    for moves in (["f7f8"], []):
        claim_snapshot(db_session, PlaySnapshot.capture(play, game))
        game.moves = moves
        db_session.add(game)
        db_session.commit()
    assert chess.Board(game.headers["FEN"]).fen() == verdict.json()["fen"]
    response = client.post(
        f"/v1/play-sessions/{play.id}/moves",
        headers=headers,
        json={"move": "f7g7", "expected_generation": verdict.json()["generation"]},
    )
    assert response.status_code == 409
    db_session.expire_all()
    stored = db_session.get(Game, game.id)
    assert stored is not None and stored.moves == []
    resumed = client.get(f"/v1/play-sessions/{play.id}", headers=headers).json()
    assert resumed["status"] == "active" and resumed["assistance_modes"] == ["guard"]


def test_stale_first_move_does_not_create_an_empty_game(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _register(db_session)
    play = PlaySession(
        player_id=OWNER,
        assistance_tracking_version=1,
        opponent_config={"player_color": "white", "assistance_generation": "0"},
    )
    db_session.add(play)
    db_session.commit()
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint", AsyncMock(side_effect=_witness)
    )
    verdict = _request(client, play, headers)
    assert verdict.status_code == 200, verdict.text
    claim_snapshot(db_session, PlaySnapshot.capture(play, None))
    db_session.commit()
    response = client.post(
        f"/v1/play-sessions/{play.id}/moves",
        headers=headers,
        json={"move": "e2e4", "expected_generation": verdict.json()["generation"]},
    )
    assert response.status_code == 409
    db_session.expire_all()
    stored = db_session.get(PlaySession, play.id)
    assert stored is not None and stored.game_id is None
    assert db_session.exec(select(Game)).all() == []
    assert client.get(f"/v1/play-sessions/{play.id}", headers=headers).json()[
        "assistance_modes"
    ] == ["candidates"]


@pytest.mark.parametrize("generation", [True, -1, 0.5, "0"])
def test_invalid_generation_is_rejected_before_first_game_creation(
    client: TestClient, db_session: Session, generation: object
) -> None:
    headers = _register(db_session)
    play = PlaySession(player_id=OWNER)
    db_session.add(play)
    db_session.commit()
    response = client.post(
        f"/v1/play-sessions/{play.id}/moves",
        headers=headers,
        json={"move": "e2e4", "expected_generation": generation},
    )
    assert response.status_code == 422
    db_session.expire_all()
    stored = db_session.get(PlaySession, play.id)
    assert stored is not None and stored.game_id is None
    assert db_session.exec(select(Game)).all() == []
