import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from scan64.chess.games.models import PlaySession  # noqa: F401
from scan64.chess.positions.models import Position  # noqa: F401
from scan64.chess.analysis.models import EngineAnalysis, AnalysisJob  # noqa: F401
from scan64.api.middleware import IdempotencyRecord  # noqa: F401
from scan64.api.models import Player, PlayerProfile  # noqa: F401
from scan64.api.app import app
from scan64.chess.games.models import Game
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
    # Also override for middleware which takes the callable directly
    # Wait, the middleware uses get_session from database.py directly if passed like that
    # We should reconstruct the app middleware for test or make sure it uses the override
    # Since we passed `get_session_callable=get_session` at module level, it's bound.
    # We can patch it here just for this test
    client = TestClient(app)

    # Let's mock the middleware's get_session directly
    for middleware in app.user_middleware:

        def mock_get_session():
            yield session

        middleware.kwargs["get_session_callable"] = mock_get_session

    yield client
    app.dependency_overrides.clear()


def test_idempotency(client: TestClient, session: Session):
    pgn = '[Event "Casual Game"]\n\n1. e4 e5'
    idem_key = str(uuid.uuid4())

    # First request
    response1 = client.post("/v1/games", json={"pgn": pgn}, headers={"Idempotency-Key": idem_key})
    assert response1.status_code == 200
    data1 = response1.json()

    # Check DB has exactly one game
    games = session.exec(select(Game)).all()
    assert len(games) == 1

    # Second request with same key
    response2 = client.post("/v1/games", json={"pgn": pgn}, headers={"Idempotency-Key": idem_key})
    assert response2.status_code == 200
    data2 = response2.json()

    # Should be exact same response
    assert data1["id"] == data2["id"]

    # DB should STILL have exactly one game
    games = session.exec(select(Game)).all()
    assert len(games) == 1

    # Check with different key
    idem_key2 = str(uuid.uuid4())
    response3 = client.post("/v1/games", json={"pgn": pgn}, headers={"client_move_id": idem_key2})
    assert response3.status_code == 200
    assert response3.json()["id"] != data1["id"]

    # DB should now have 2 games
    games = session.exec(select(Game)).all()
    assert len(games) == 2
