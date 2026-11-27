"""POST /v1/analysis/position/health: factor meters with no engine or model (#416)."""

from __future__ import annotations

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player, PlayerCredential, issue_player_token


def _register(db_session: Session) -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id="health-player"))
    db_session.add(PlayerCredential(player_id="health-player", token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_position_health_reads_every_group_for_both_sides(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session)
    after = chess.Board()
    after.push_san("e4")
    response = client.post(
        "/v1/analysis/position/health",
        json={"fen": after.fen(), "learner": "white", "previous_fen": chess.STARTING_FEN},
        headers=headers,
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["learner"] == "white"
    assert [g["name"] for g in body["groups"]] == [
        "king safety",
        "material",
        "threats",
        "piece placement",
        "mobility",
        "pawn structure",
        "passed pawns",
        "space",
    ]
    assert body["overall"]["name"] == "overall"
    assert body["overall"]["learner"]["level"] == "good"
    mobility = next(g for g in body["groups"] if g["key"] == "Mobility")
    assert mobility["learner"]["trend"] == "rising"
    assert 0 <= mobility["learner"]["health"] <= 1
    assert body["outcome"] is None


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        ("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3", "checkmate"),
        ("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", "stalemate"),
        ("8/8/8/8/8/8/1KB5/7k w - - 0 60", "insufficient_material"),
        ("8/5pk1/4p3/8/8/1P6/1P3K2/R6r w - - 150 120", "seventyfive_moves"),
        # Only claimable: the game, and the meters, go on.
        ("8/5pk1/4p3/8/8/1P6/1P3K2/R6r w - - 100 120", None),
    ],
)
def test_position_health_reports_a_finished_game(
    client: TestClient, db_session: Session, fen: str, expected: str | None
) -> None:
    headers = _register(db_session)
    response = client.post(
        "/v1/analysis/position/health", json={"fen": fen, "learner": "white"}, headers=headers
    )
    assert response.status_code == 200, response.json()
    assert response.json()["outcome"] == expected


def test_position_health_refuses_a_bad_fen_and_anonymous_callers(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session)
    bad = client.post(
        "/v1/analysis/position/health", json={"fen": "nope", "learner": "white"}, headers=headers
    )
    assert bad.status_code == 422
    anonymous = client.post(
        "/v1/analysis/position/health", json={"fen": chess.STARTING_FEN, "learner": "white"}
    )
    assert anonymous.status_code == 401
