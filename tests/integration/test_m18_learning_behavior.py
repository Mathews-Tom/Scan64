from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
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


def authorize(db_session: Session, player_id: str) -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_player_scoped_persisted_opportunities(client: TestClient, db_session: Session) -> None:
    player_1 = f"player_{uuid4()}"
    player_2 = f"player_{uuid4()}"

    player_1_headers = authorize(db_session, player_1)
    player_2_headers = authorize(db_session, player_2)

    # Create games
    g1 = Game(pgn="", white="P1", black="P2", result="*")
    g2 = Game(pgn="", white="P3", black="P4", result="*")
    db_session.add(g1)
    db_session.add(g2)
    db_session.commit()
    db_session.refresh(g1)
    db_session.refresh(g2)
    source_position_1 = Position(
        game_id=g1.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial",
    )
    source_position_2 = Position(
        game_id=g2.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial",
    )
    db_session.add(source_position_1)
    db_session.add(source_position_2)
    db_session.commit()
    db_session.add_all(
        [
            EngineAnalysis(
                position_id=source_position_1.id,
                raw_result=[{"pv": ["e2e4", "e7e5"]}],
            ),
            EngineAnalysis(
                position_id=source_position_2.id,
                raw_result=[{"pv": ["e2e4", "e7e5"]}],
            ),
        ]
    )
    db_session.commit()

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
        "source": {
            "kind": "player_game",
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        },
        "diagnosis": {"primary": "tactics", "confidence": 1.0},
        "objective": {"type": "find_best_move", "instruction": "Find it"},
        "interaction": {"input": "click", "maximum_attempts": 3, "accepted_moves": [{"san": "e4"}]},
        "verification": {"status": "verified", "engine": "syzygy"},
    }
    spec2 = {
        "schema_version": "1.0",
        "lesson_id": f"lesson_{uuid4()}",
        "source": {
            "kind": "player_game",
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        },
        "diagnosis": {"primary": "tactics", "confidence": 1.0},
        "objective": {"type": "find_best_move", "instruction": "Find it"},
        "interaction": {"input": "click", "maximum_attempts": 3, "accepted_moves": [{"san": "e4"}]},
        "verification": {"status": "verified", "engine": "syzygy"},
    }
    opp1 = PersistedLessonOpportunity(
        game_id=g1.id,
        source_position_id=source_position_1.id,
        player_id=player_1,
        lesson_spec=spec1,
    )
    opp2 = PersistedLessonOpportunity(
        game_id=g2.id,
        source_position_id=source_position_2.id,
        player_id=player_2,
        lesson_spec=spec2,
    )
    db_session.add(opp1)
    db_session.add(opp2)
    db_session.commit()
    db_session.add(
        ReviewSchedule(player_id=player_1, item_id=str(opp1.id), next_review_at=datetime.now(UTC))
    )
    db_session.add(
        ReviewSchedule(player_id=player_2, item_id=str(opp2.id), next_review_at=datetime.now(UTC))
    )
    db_session.commit()
    # Query for player_1
    resp1 = client.get(f"/v1/learning/session?player_id={player_1}", headers=player_1_headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    lesson_ids_1 = [item["lesson_id"] for item in data1["lessons"]]
    assert str(opp1.id) in lesson_ids_1
    assert str(opp2.id) not in lesson_ids_1

    # Query for player_2
    resp2 = client.get(f"/v1/learning/session?player_id={player_2}", headers=player_2_headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    lesson_ids_2 = [item["lesson_id"] for item in data2["lessons"]]
    assert str(opp2.id) in lesson_ids_2
    assert str(opp1.id) not in lesson_ids_2


def test_static_catalog_items_are_not_served_to_profile_training(
    client: TestClient, db_session: Session
) -> None:
    player_id = str(uuid4())
    headers = authorize(db_session, player_id)
    response = client.get(f"/v1/learning/session?player_id={player_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["lessons"] == []


def test_due_schedule_survives_sqlite_datetime_round_trip(
    client: TestClient, db_session: Session
) -> None:
    player_id = f"player_{uuid4()}"
    headers = authorize(db_session, player_id)
    now = datetime.now(UTC)
    opportunities: list[PersistedLessonOpportunity] = []
    for offset in (timedelta(days=1), timedelta(days=-1)):
        game = Game(pgn="", owner_player_id=player_id)
        db_session.add(game)
        db_session.flush()
        source_position = Position(
            game_id=game.id,
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            side_to_move="w",
            canonical_id="initial",
        )
        db_session.add(source_position)
        db_session.flush()
        db_session.add(
            EngineAnalysis(
                position_id=source_position.id,
                raw_result=[{"pv": ["e2e4", "e7e5"]}],
            )
        )
        opportunity = PersistedLessonOpportunity(
            game_id=game.id,
            source_position_id=source_position.id,
            player_id=player_id,
            lesson_spec={
                "schema_version": "1.0",
                "lesson_id": f"lesson-{uuid4()}",
                "source": {
                    "kind": "player_game",
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                },
                "diagnosis": {"primary": "tactics", "confidence": 1.0},
                "objective": {"type": "find_best_move", "instruction": "Find it"},
                "interaction": {
                    "input": "click",
                    "maximum_attempts": 3,
                    "accepted_moves": [{"san": "e4"}],
                },
                "verification": {"status": "verified", "engine": "syzygy"},
            },
        )
        db_session.add(opportunity)
        db_session.flush()
        db_session.add(
            ReviewSchedule(
                player_id=player_id,
                item_id=str(opportunity.id),
                next_review_at=now + offset,
            )
        )
        opportunities.append(opportunity)
    db_session.commit()
    db_session.expire_all()

    response = client.get(f"/v1/learning/session?player_id={player_id}", headers=headers)

    assert response.status_code == 200
    lesson_ids = [item["lesson_id"] for item in response.json()["lessons"]]
    assert lesson_ids.index(str(opportunities[1].id)) < lesson_ids.index(str(opportunities[0].id))
