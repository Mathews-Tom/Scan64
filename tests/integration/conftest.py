import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from scan64.api.app import app
import scan64.persistence.database as db_module

# Shared in-memory DB with StaticPool so all connections use the same DB
test_engine = create_engine(
    "sqlite://", 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
db_module.engine = test_engine

@pytest.fixture(name="db_session")
def session_fixture():
    # Rely on the TestClient lifespan to create tables.
    with Session(test_engine) as session:
        yield session

@pytest.fixture(name="client")
def client_fixture(db_session: Session):
    def get_session_override():
        return db_session
    
    app.dependency_overrides[db_module.get_session] = get_session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
