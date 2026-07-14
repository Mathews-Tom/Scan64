from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from scan64.api.app import app
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.persistence import database


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

@pytest.fixture
def client(db_session: Session) -> TestClient:
    def override_get_session():
        yield db_session

    app.dependency_overrides[database.get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_player_scoped_persisted_opportunities(client: TestClient, db_session: Session) -> None:
    player_1 = f"player_{uuid4()}"
    player_2 = f"player_{uuid4()}"

    # Create games
    g1 = Game(pgn="", white="P1", black="P2", result="*")
    g2 = Game(pgn="", white="P3", black="P4", result="*")
    db_session.add(g1)
    db_session.add(g2)
    db_session.commit()
    db_session.refresh(g1)
    db_session.refresh(g2)

    # Create sessions
    ps1 = PlaySession(player_id=player_1, game_id=g1.id)
    ps2 = PlaySession(player_id=player_2, game_id=g2.id)
    db_session.add(ps1)
    db_session.add(ps2)
    db_session.commit()

    # Create opportunities
    spec1 = {
        "schema_version": "1.0",
        "lesson_id": f"lesson_{uuid4()}",
        "source": {"kind": "player_game", "fen": "8/8/8/8/8/8/8/8 w - - 0 1"},
        "diagnosis": {"primary": "tactics", "confidence": 1.0},
        "objective": {"type": "find_best_move", "instruction": "Find it"},
        "interaction": {"input": "click", "maximum_attempts": 3, "accepted_moves": [{"san": "e4"}]},
        "verification": {"status": "verified", "engine": "syzygy"}
    }
    spec2 = {
        "schema_version": "1.0",
        "lesson_id": f"lesson_{uuid4()}",
        "source": {"kind": "player_game", "fen": "8/8/8/8/8/8/8/8 w - - 0 1"},
        "diagnosis": {"primary": "tactics", "confidence": 1.0},
        "objective": {"type": "find_best_move", "instruction": "Find it"},
        "interaction": {"input": "click", "maximum_attempts": 3, "accepted_moves": [{"san": "e4"}]},
        "verification": {"status": "verified", "engine": "syzygy"}
    }

    opp1 = PersistedLessonOpportunity(game_id=g1.id, lesson_spec=spec1)
    opp2 = PersistedLessonOpportunity(game_id=g2.id, lesson_spec=spec2)
    db_session.add(opp1)
    db_session.add(opp2)
    db_session.commit()
    # Query for player_1
    resp1 = client.get(f"/v1/learning/session?player_id={player_1}")
    assert resp1.status_code == 200
    data1 = resp1.json()

    # Check if spec1 is present but not spec2
    lesson_ids_1 = [item["lesson_id"] for item in data1]
    assert spec1["lesson_id"] in lesson_ids_1
    assert spec2["lesson_id"] not in lesson_ids_1

    # Query for player_2
    resp2 = client.get(f"/v1/learning/session?player_id={player_2}")
    assert resp2.status_code == 200
    data2 = resp2.json()

    lesson_ids_2 = [item["lesson_id"] for item in data2]
    assert spec2["lesson_id"] in lesson_ids_2
    assert spec1["lesson_id"] not in lesson_ids_2


def test_actual_transfer_selection(client: TestClient) -> None:
    # A new player hasn't seen any famous games, so they are not due.
    # They should be classified as "transfer" and have high priority.
    player_id = f"new_player_{uuid4()}"

    # Let's get the learning session directly
    # Since composer mix is due: 0.4, mistakes: 0.3, transfer: 0.2, exploration: 0.1
    # For session_size 5:
    # due: 2
    # mistakes: 2
    # transfer: 1
    # exploration: 1 -> wait, ceil means total is 6, shrinks to 5
    # Since this player has no due or mistakes, remaining pool fills it.
    # Famous games have base_priority=0.6, exploration=0.5
    # So famous games should be present and take up at least the transfer bucket
    # (and probably more).

    resp = client.get(f"/v1/learning/session?player_id={player_id}")
    assert resp.status_code == 200
    data = resp.json()

    # We should have at least one famous game in the session
    lesson_ids = [item["lesson_id"] for item in data]
    famous_game_prefix = "morphy-" # all current famous games start with morphy-
    has_famous = any(lid.startswith(famous_game_prefix) for lid in lesson_ids)

    assert has_famous, f"Famous game not selected in session. Lesson IDs: {lesson_ids}"

