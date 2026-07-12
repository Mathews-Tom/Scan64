import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from scan64.api.app import app
from scan64.chess.games.models import PlaySession
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
