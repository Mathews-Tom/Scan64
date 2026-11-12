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
from scan64.api.play import get_opponent_provider
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.opponents.protocols import MoveDecision

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
        json={"player_id": "hint-owner", "fen": record["fen"], "step": 3},
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
    body = {"player_id": "hint-owner", "fen": chess.STARTING_FEN, "step": 3}
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
        json={"player_id": "hint-owner", "play_session_id": str(play.id), "step": 3},
    )
    missing_session = client.post(
        "/v1/insights/position",
        headers=owner,
        json={"player_id": "hint-owner", "play_session_id": str(uuid4()), "step": 3},
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
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "fen": fen, "step": 3},
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
        json={"player_id": "hint-owner", "play_session_id": str(play.id), "step": 3},
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
    body = {"player_id": "hint-owner", "play_session_id": str(play.id), "step": 3}
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
                "/v1/insights/position",
                headers=headers,
                json={"player_id": "hint-owner", "step": 3, **source},
            ).status_code
            == 422
        )


def test_busy_and_disabled_pool_are_explicit(client: TestClient, db_session: Session, monkeypatch):
    headers = register(db_session, "hint-owner")
    body = {"player_id": "hint-owner", "fen": chess.STARTING_FEN, "step": 3}
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
        json={"player_id": "hint-owner", "fen": chess.STARTING_FEN, "step": 3},
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
        json={"player_id": "hint-owner", "play_session_id": play["id"], "step": 3},
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
        json={"player_id": "hint-owner", "fen": fen, "step": 3},
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
        json={"player_id": "hint-owner", "play_session_id": str(play.id), "step": 3},
    )
    assert response.status_code == expected, response.text
    assert analyze.await_count == (1 if expected == 200 else 0)


@pytest.mark.parametrize("name", ["A", "C", "D"])
@pytest.mark.parametrize("step", [1, 2, 3])
def test_only_requested_step_is_serialized(client, db_session, monkeypatch, name, step):
    from scan64.learning.insights import position_insight

    headers = register(db_session, "hint-owner")
    record = RECORDING["positions"][name]
    analysis = EngineAnalysis(raw_result=record["raw_result"])
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint", AsyncMock(return_value=analysis)
    )
    response = client.post(
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "fen": record["fen"], "step": step},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["step"] == step
    if step == 3:
        assert body["candidates"]
        return
    assert "candidates" not in body
    assert "fen" not in body
    if step == 1:
        assert len(body["visualizations"]) == 1
        assert all(drawing["command"] == "highlight_square" for drawing in body["visualizations"])
    else:
        assert body["piece"]
    serialized = json.dumps(body)
    for candidate in position_insight(chess.Board(record["fen"]), analysis).candidates:
        assert candidate.san not in serialized
        assert candidate.move not in serialized
        assert candidate.move[2:4] not in serialized
        if step == 1:
            squares = {drawing["square"] for drawing in body["visualizations"]}
            assert squares.isdisjoint({candidate.move[:2], candidate.move[2:4]})


def test_quiet_pawn_ladder_starts_with_a_safe_cue_without_identifying_the_piece(
    client: TestClient, db_session: Session, monkeypatch
):
    headers = register(db_session, "hint-owner")
    fen = "r1bq1rk1/pppp1ppp/2n2n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R w KQ - 0 6"
    analysis = EngineAnalysis(
        raw_result=[
            {"pv": ["h2h3"], "score_cp": 31},
            {"pv": ["h2h4"], "score_cp": 24},
        ]
    )
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint", AsyncMock(return_value=analysis)
    )
    responses = []
    for step in (1, 2, 3):
        response = client.post(
            "/v1/insights/position",
            headers=headers,
            json={"player_id": "hint-owner", "fen": fen, "step": step},
        )
        assert response.status_code == 200, response.text
        responses.append(response.json())
    assert responses[0] == {
        "step": 1,
        "text": "How is the highlighted pawn defended?",
        "visualizations": [
            {
                "command": "highlight_square",
                "description": "Inspect this square.",
                "square": "e5",
            }
        ],
    }
    assert responses[1]["square"] == "h2"
    assert [candidate["move"] for candidate in responses[2]["candidates"]] == ["h2h3", "h2h4"]
    endpoints = {
        square
        for candidate in responses[2]["candidates"]
        for square in (candidate["move"][:2], candidate["move"][2:4])
    }
    nudge_squares = {drawing["square"] for drawing in responses[0]["visualizations"]}
    assert nudge_squares.isdisjoint(endpoints | {responses[1]["square"]})


@pytest.mark.parametrize("step", [None, 0, 4, "moves"])
def test_step_is_required_and_bounded(client, db_session, step):
    headers = register(db_session, "hint-owner")
    body = {"player_id": "hint-owner", "fen": chess.STARTING_FEN}
    if step is not None:
        body["step"] = step
    assert client.post("/v1/insights/position", headers=headers, json=body).status_code == 422


def test_black_hint_before_human_first_move_and_after_takeback(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session, "hint-owner")
    opponent = AsyncMock()
    opponent.choose_move.side_effect = [
        MoveDecision(uci_move="e2e4"),
        MoveDecision(uci_move="g1f3"),
    ]
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    created = client.post(
        "/v1/play-sessions",
        headers=headers,
        json={
            "player_id": "hint-owner",
            "player_color": "black",
            "opponent_config": {"bot_level": "1"},
        },
    )
    assert created.status_code == 200, created.text
    play = created.json()
    assert play["opponent_move"] == "e2e4"
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e7e5"], "score_cp": 0}]))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    board = chess.Board()
    board.push_uci("e2e4")
    body = {"player_id": "hint-owner", "play_session_id": play["id"], "step": 3}
    first = client.post("/v1/insights/position", headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["fen"] == board.fen()
    assert (
        client.post(
            f"/v1/play-sessions/{play['id']}/moves", headers=headers, json={"move": "e7e5"}
        ).status_code
        == 200
    )
    assert (
        client.post(f"/v1/play-sessions/{play['id']}/takeback", headers=headers).status_code == 200
    )
    repeated = client.post("/v1/insights/position", headers=headers, json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["fen"] == board.fen()
    assert analyze.await_count == 2
    assert all(call.args == (board.fen(),) for call in analyze.await_args_list)


@pytest.mark.parametrize(
    ("color", "moves", "expected"),
    [("black", ["e2e4"], 200), ("black", [], 409), (None, ["e2e4"], 200)],
)
def test_hint_uses_session_color_before_owner_color_with_legacy_fallback(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    color: str | None,
    moves: list[str],
    expected: int,
) -> None:
    headers = register(db_session, "hint-owner")
    game = Game(
        pgn="*",
        moves=moves,
        owner_player_id="hint-owner",
        owner_color="white" if color is not None else None,
    )
    db_session.add(game)
    db_session.flush()
    play = PlaySession(
        player_id="hint-owner",
        game_id=game.id,
        opponent_config={"player_color": color} if color is not None else {},
    )
    db_session.add(play)
    db_session.commit()
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e7e5"], "score_cp": 0}]))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(
        "/v1/insights/position",
        headers=headers,
        json={"player_id": "hint-owner", "play_session_id": str(play.id), "step": 3},
    )
    assert response.status_code == expected, response.text
    assert analyze.await_count == (1 if expected == 200 else 0)
