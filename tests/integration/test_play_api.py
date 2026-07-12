import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from scan64.api.app import app
from scan64.persistence.database import get_session
from scan64.chess.games.models import PlaySession

@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
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
