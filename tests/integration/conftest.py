from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

import scan64.persistence.database as db_module
from scan64.api.app import app


@pytest.fixture(name="db_session")
def session_fixture(tmp_path: Path) -> Iterator[Session]:
    previous_engine = db_module.engine
    # Scheduler and request/test sessions need independent SQLite connections.
    test_engine = db_module.create_database_engine(f"sqlite:///{tmp_path / 'integration.db'}")
    db_module.engine = test_engine
    try:
        SQLModel.metadata.create_all(test_engine)
        with Session(test_engine) as session:
            yield session
    finally:
        db_module.engine = previous_engine
        test_engine.dispose()


@pytest.fixture(name="client")
def client_fixture(db_session: Session) -> Iterator[TestClient]:
    def get_session_override() -> Session:
        return db_session

    app.dependency_overrides[db_module.get_session] = get_session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
