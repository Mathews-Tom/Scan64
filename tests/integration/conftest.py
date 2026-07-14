import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import scan64.persistence.database as db_module
from scan64.api.app import app


@pytest.fixture(name="db_session")
def session_fixture():
    previous_engine = db_module.engine
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_module.engine = test_engine
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session:
            yield session
    finally:
        db_module.engine = previous_engine


@pytest.fixture(name="client")
def client_fixture(db_session: Session):
    def get_session_override():
        return db_session

    app.dependency_overrides[db_module.get_session] = get_session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
