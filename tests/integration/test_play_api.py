from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from scan64.api.app import app
from scan64.api.middleware import IdempotencyRecord  # noqa: F401
from scan64.api.models import Player, PlayerProfile  # noqa: F401
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis  # noqa: F401
from scan64.chess.games.models import (
    Game,  # noqa: F401
    PlaySession,
)
from scan64.chess.positions.models import Position  # noqa: F401
from scan64.persistence.database import get_session


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    from scan64.persistence import database

    database.engine = engine
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session

    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_play_session(session: Session):
    play_session = PlaySession(player_id="test_player")
    session.add(play_session)
    session.commit()
    session.refresh(play_session)
    assert play_session.id is not None
    assert play_session.player_id == "test_player"
    assert play_session.status == "active"


def test_create_get_game(client: TestClient):
    pgn = (
        '[Event "Casual Game"]\n[White "Alice"]\n[Black "Bob"]\n'
        '[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0'
    )
    response = client.post("/v1/games", json={"pgn": pgn})
    assert response.status_code == 200
    data = response.json()
    assert data["white"] == "Alice"
    assert data["black"] == "Bob"
    assert data["result"] == "1-0"

    game_id = data["id"]
    response2 = client.get(f"/v1/games/{game_id}")
    assert response2.status_code == 200
    assert response2.json()["id"] == game_id


def test_create_get_analysis_job(client: TestClient):
    pgn = '[Event "Casual Game"]\n\n1. e4 e5 2. Nf3 Nc6'
    game_response = client.post("/v1/games", json={"pgn": pgn})
    game_id = game_response.json()["id"]

    job_response = client.post(f"/v1/games/{game_id}/analysis-jobs")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["game_id"] == game_id
    assert job_data["status"] == "pending"

    job_id = job_data["id"]
    get_job_response = client.get(f"/v1/analysis-jobs/{job_id}")
    assert get_job_response.status_code == 200
    assert get_job_response.json()["status"] == "completed"


def test_create_and_get_play_session_api(client: TestClient):
    req_body = {
        "player_id": "player_123",
        "opponent_config": {"strength": "10"},
        "clock_config": {"time_remaining_ms": "60000"},
    }
    create_resp = client.post("/v1/play-sessions", json=req_body)
    assert create_resp.status_code == 200, create_resp.text
    session_data = create_resp.json()
    assert session_data["player_id"] == "player_123"
    assert session_data["status"] == "active"

    session_id = session_data["id"]
    get_resp = client.get(f"/v1/play-sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == session_id


def test_create_play_session_persists_initial_fen(client: TestClient, session: Session) -> None:
    initial_fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

    response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "player_123",
            "opponent_config": {"strength": "10"},
            "initial_fen": initial_fen,
        },
    )

    assert response.status_code == 200, response.text
    game_id = response.json()["game_id"]
    assert game_id is not None
    game = session.get(Game, UUID(game_id))
    assert game is not None
    assert game.headers == {"FEN": initial_fen}


def test_create_play_session_rejects_invalid_initial_fen(client: TestClient) -> None:
    response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "player_123",
            "opponent_config": {"strength": "10"},
            "initial_fen": "not a FEN",
        },
    )

    assert response.status_code == 422


def test_play_session_moves_api(client: TestClient):
    req_body = {"player_id": "player_123", "opponent_config": {"strength": "10"}}
    create_resp = client.post("/v1/play-sessions", json=req_body)
    session_id = create_resp.json()["id"]

    # First move
    move_req = {"move": "e2e4"}
    move_resp = client.post(
        f"/v1/play-sessions/{session_id}/moves", json=move_req, headers={"Idempotency-Key": "move1"}
    )
    assert move_resp.status_code == 200, move_resp.text
    move_data = move_resp.json()
    assert "opponent_move" in move_data
    assert move_data["opponent_move"] is not None

    # Retry first move (idempotency)
    retry_resp = client.post(
        f"/v1/play-sessions/{session_id}/moves", json=move_req, headers={"Idempotency-Key": "move1"}
    )
    assert retry_resp.status_code == 200
    assert retry_resp.json() == move_data

    # Illegal move
    illegal_resp = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e5"},
        headers={"Idempotency-Key": "move2"},
    )
    assert illegal_resp.status_code == 400
