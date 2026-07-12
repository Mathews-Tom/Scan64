
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from scan64.api.app import app
from scan64.api.middleware import IdempotencyRecord  # noqa: F401
from scan64.api.models import Player, PlayerProfile  # noqa: F401
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis  # noqa: F401
from scan64.chess.games.models import Game, PlaySession  # noqa: F401
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

    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "IdempotencyMiddleware":

            def mock_get_session():
                yield session

            middleware.kwargs["get_session_callable"] = mock_get_session

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_pagination(client: TestClient, session: Session):
    # Create 15 games
    for i in range(15):
        pgn = f'[Event "Game {i}"]\n\n1. e4 e5'
        game = Game(pgn=pgn, moves=["e4", "e5"])
        session.add(game)
    session.commit()

    # Get page 1 (limit 10)
    response1 = client.get("/v1/games?limit=10")
    assert response1.status_code == 200
    data1 = response1.json()
    assert len(data1["items"]) == 10
    assert data1["next_cursor"] is not None

    # Get page 2
    response2 = client.get(f"/v1/games?limit=10&cursor={data1['next_cursor']}")
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2["items"]) == 5
    assert data2["next_cursor"] is None


def test_players(client: TestClient, session: Session):
    response = client.post("/v1/players", json={"id": "player_123", "display_name": "Test Player"})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "player_123"

    # Get profile
    profile_resp = client.get("/v1/players/player_123/profile")
    assert profile_resp.status_code == 200
    profile_data = profile_resp.json()
    assert profile_data["display_name"] == "Test Player"
    assert profile_data["rating"] == 1500
