from collections.abc import Generator

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from scan64.learning.evidence.models import Evidence


@pytest.fixture(name="session")
def session_fixture() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_evidence_creation_and_persistence(session: Session) -> None:
    evidence = Evidence(
        evidence_id="ev_1",
        kind="engine_line",
        engine_analysis_id="ea_101",
        position_id="pos_27",
        claim="Move c6d4 creates a double attack",
        payload={
            "move": "c6d4",
            "targets": ["e2", "f3"],
            "principal_variation": ["c6d4", "f3d4"],
        },
        producer={
            "name": "stockfish_adapter",
            "version": "0.1.0",
        },
    )

    session.add(evidence)
    session.commit()
    session.refresh(evidence)

    assert evidence.evidence_id == "ev_1"
    assert evidence.kind == "engine_line"
    assert evidence.position_id == "pos_27"
    assert evidence.claim == "Move c6d4 creates a double attack"
    assert evidence.payload["move"] == "c6d4"
    assert evidence.producer["name"] == "stockfish_adapter"

    # Retrieve from DB
    retrieved = session.exec(select(Evidence).where(Evidence.evidence_id == "ev_1")).first()
    assert retrieved is not None
    assert retrieved.evidence_id == "ev_1"
    assert retrieved.payload["principal_variation"] == ["c6d4", "f3d4"]
