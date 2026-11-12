import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.games.models import Game, PlaySession

RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/hint_engine_recording.json").read_text()
)


def register(session: Session, player_id: str) -> dict[str, str]:
    token, hashed = issue_player_token()
    session.add(Player(id=player_id))
    session.add(PlayerCredential(player_id=player_id, token_hash=hashed))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("name", ["A", "C", "D"])
def test_recorded_insight_response(client: TestClient, db_session: Session, monkeypatch, name):
    headers = register(db_session, "hint-owner")
    record = RECORDING["positions"][name]
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=record["raw_result"]))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "fen": record["fen"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert 1 <= len(body["candidates"]) <= 3
    assert body["visualizations"][0]["command"] == "draw_arrow"
    assert "origin" in body["visualizations"][0]
    for candidate in body["candidates"]:
        assert candidate["fallback"] == (not candidate["reasons"])
        for reason in candidate["reasons"]:
            assert reason["line"] == candidate["line"][: len(reason["line"])]
    analyze.assert_awaited_once_with(record["fen"])


def test_authentication_ownership_and_not_found(client: TestClient, db_session: Session):
    owner = register(db_session, "hint-owner")
    other = register(db_session, "hint-other")
    body = {"player_id": "hint-owner", "fen": chess.STARTING_FEN}
    assert client.post("/v1/insights/position", json=body).status_code == 401
    assert (
        client.post(
            "/v1/insights/position", json=body, headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    foreign = client.post("/v1/insights/position", json=body, headers=other)
    missing = client.post(
        "/v1/insights/position", json={**body, "player_id": "missing"}, headers=owner
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    play = PlaySession(player_id="hint-other")
    db_session.add(play)
    db_session.commit()
    foreign_session = client.post(
        "/v1/insights/position",
        headers=owner,
        json={"player_id": "hint-owner", "play_session_id": str(play.id)},
    )
    missing_session = client.post(
        "/v1/insights/position",
        headers=owner,
        json={"player_id": "hint-owner", "play_session_id": str(uuid4())},
    )
    assert foreign_session.status_code == missing_session.status_code == 404
    assert foreign_session.json() == missing_session.json()


@pytest.mark.parametrize(
    ("fen", "status"),
    [
        ("not a FEN", 422),
        ("8/8/8/8/8/8/8/8 w - - 0 1", 422),
        ("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", 409),
        ("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", 409),
        ("7k/8/6K1/8/8/8/8/8 w - - 0 1", 409),
    ],
)
def test_illegal_and_game_over_fens_rejected(client: TestClient, db_session: Session, fen, status):
    headers = register(db_session, "hint-owner")
    response = client.post(
        "/v1/insights/position", headers=headers, json={"player_id": "hint-owner", "fen": fen}
    )
    assert response.status_code == status


def test_finished_session_rejected(client: TestClient, db_session: Session):
    headers = register(db_session, "hint-owner")
    play = PlaySession(player_id="hint-owner", status="completed")
    db_session.add(play)
    db_session.commit()
    response = client.post(
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "play_session_id": str(play.id)},
    )
    assert response.status_code == 409


def test_active_session_uses_current_position(client: TestClient, db_session: Session, monkeypatch):
    headers = register(db_session, "hint-owner")
    record = RECORDING["positions"]["A"]
    game = Game(
        pgn="*", headers={"FEN": record["fen"]}, owner_player_id="hint-owner", owner_color="white"
    )
    db_session.add(game)
    db_session.flush()
    play = PlaySession(player_id="hint-owner", game_id=game.id)
    db_session.add(play)
    db_session.commit()
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=record["raw_result"]))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    body = {"player_id": "hint-owner", "play_session_id": str(play.id)}
    response = client.post("/v1/insights/position", headers=headers, json=body)
    assert response.status_code == 200
    analyze.assert_awaited_once_with(record["fen"])
    game.moves = ["f1c4"]
    db_session.add(game)
    db_session.commit()
    assert client.post("/v1/insights/position", headers=headers, json=body).status_code == 409


def test_source_is_exclusive(client: TestClient, db_session: Session):
    headers = register(db_session, "hint-owner")
    for source in ({}, {"fen": chess.STARTING_FEN, "play_session_id": str(uuid4())}):
        assert (
            client.post(
                "/v1/insights/position", headers=headers, json={"player_id": "hint-owner", **source}
            ).status_code
            == 422
        )


def test_busy_and_disabled_pool_are_explicit(client: TestClient, db_session: Session, monkeypatch):
    headers = register(db_session, "hint-owner")
    body = {"player_id": "hint-owner", "fen": chess.STARTING_FEN}
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint", AsyncMock(side_effect=TimeoutError)
    )
    busy = client.post("/v1/insights/position", headers=headers, json=body)
    assert busy.status_code == 503
    assert "try again" in busy.json()["detail"]
    monkeypatch.setattr(app.state, "engine_pool_manager", None)
    disabled = client.post("/v1/insights/position", headers=headers, json=body)
    assert disabled.status_code == 503
    assert "interactive engine pool" in disabled.json()["detail"]


def test_single_engine_is_explicitly_unavailable(
    client: TestClient, db_session: Session, monkeypatch
):
    headers = register(db_session, "hint-owner")
    monkeypatch.setattr(app.state.engine_pool_manager.interactive_pool, "concurrency", 1)
    response = client.post(
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "fen": chess.STARTING_FEN},
    )
    assert response.status_code == 503
    assert "at least two" in response.json()["detail"]


@pytest.mark.parametrize("initial_fen", [None, RECORDING["positions"]["A"]["fen"]])
def test_hint_before_first_move_uses_session_start(
    client: TestClient, db_session: Session, monkeypatch, initial_fen
):
    headers = register(db_session, "hint-owner")
    created = client.post(
        "/v1/play-sessions",
        headers=headers,
        json={"player_id": "hint-owner", "initial_fen": initial_fen},
    )
    assert created.status_code == 200
    play = created.json()
    fen = initial_fen or chess.STARTING_FEN
    lines = (
        RECORDING["positions"]["A"]["raw_result"]
        if initial_fen
        else [{"pv": ["g1f3", "g8f6"], "score_cp": 0}]
    )
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=lines))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "play_session_id": play["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["fen"] == fen
    analyze.assert_awaited_once_with(fen)
    if initial_fen is None:
        assert play["game_id"] is None
        resumed = client.get(f"/v1/play-sessions/{play['id']}", headers=headers)
        assert resumed.json()["game_id"] is None


@pytest.mark.parametrize(("halfmove_clock", "expected"), [(100, 200), (150, 409)])
def test_claimable_fifty_moves_remains_playable_but_seventyfive_is_terminal(
    client: TestClient, db_session: Session, monkeypatch, halfmove_clock, expected
):
    headers = register(db_session, "hint-owner")
    fen = chess.STARTING_FEN.replace("0 1", f"{halfmove_clock} 80")
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["g1f3"], "score_cp": 0}]))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "fen": fen},
    )
    assert response.status_code == expected, response.text
    assert analyze.await_count == (1 if expected == 200 else 0)


@pytest.mark.parametrize(("cycles", "expected"), [(2, 200), (4, 409)])
def test_claimable_threefold_remains_playable_but_fivefold_is_terminal(
    client: TestClient, db_session: Session, monkeypatch, cycles, expected
):
    headers = register(db_session, "hint-owner")
    moves = ["g1f3", "g8f6", "f3g1", "f6g8"] * cycles
    game = Game(pgn="*", moves=moves, owner_player_id="hint-owner", owner_color="white")
    db_session.add(game)
    db_session.flush()
    play = PlaySession(player_id="hint-owner", game_id=game.id)
    db_session.add(play)
    db_session.commit()
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["g1f3"], "score_cp": 0}]))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "play_session_id": str(play.id)},
    )
    assert response.status_code == expected, response.text
    assert analyze.await_count == (1 if expected == 200 else 0)
