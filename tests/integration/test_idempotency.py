import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from scan64.api.app import app
from scan64.api.middleware import IdempotencyRecord  # noqa: F401
from scan64.api.models import (  # noqa: F401
    Player,
    PlayerCredential,
    PlayerProfile,
    issue_player_token,
)
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis  # noqa: F401
from scan64.chess.games.models import (
    Game,
    PlaySession,  # noqa: F401
)
from scan64.chess.positions.models import Position  # noqa: F401
from scan64.content.models import ContentAttempt
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
    token, token_hash = issue_player_token()
    player = Player(id="idempotency-player")
    session.add(player)
    session.add(PlayerCredential(player_id=player.id, token_hash=token_hash))
    session.commit()
    second_token, second_token_hash = issue_player_token()
    second_player = Player(id="other-idempotency-player")
    session.add(second_player)
    session.add(PlayerCredential(player_id=second_player.id, token_hash=second_token_hash))
    session.commit()
    pgn = '[Event "Casual Game"]\n\n1. e4 e5'
    payload = {"pgn": pgn, "player_id": player.id}
    idem_key = str(uuid.uuid4())

    # First request
    response1 = client.post(
        "/v1/games",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idem_key},
    )
    assert response1.status_code == 200
    data1 = response1.json()

    # Check DB has exactly one game
    games = session.exec(select(Game)).all()
    assert len(games) == 1

    # Second request with same key
    response2 = client.post(
        "/v1/games",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idem_key},
    )
    assert response2.status_code == 200
    data2 = response2.json()

    # Should be exact same response
    assert data1["id"] == data2["id"]

    # DB should STILL have exactly one game for the first player.
    games = session.exec(select(Game)).all()
    assert len(games) == 1

    other_response = client.post(
        "/v1/games",
        json={**payload, "player_id": second_player.id},
        headers={"Authorization": f"Bearer {second_token}", "Idempotency-Key": idem_key},
    )
    assert other_response.status_code == 200
    assert other_response.json()["id"] != data1["id"]

    unauthenticated_replay = client.post(
        "/v1/games",
        json=payload,
        headers={"Idempotency-Key": idem_key},
    )
    assert unauthenticated_replay.status_code == 401

    # Check with different key
    idem_key2 = str(uuid.uuid4())
    response3 = client.post(
        "/v1/games",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "client_move_id": idem_key2},
    )
    assert response3.status_code == 200
    assert response3.json()["id"] != data1["id"]

    # The two principals may safely reuse a key, so three games exist.
    games = session.exec(select(Game)).all()
    assert len(games) == 3


def test_idempotency_does_not_replay_anonymous_registration(
    client: TestClient, session: Session
) -> None:
    headers = {"Idempotency-Key": "shared-registration-key"}

    first = client.post("/v1/players", json={"id": "first-player"}, headers=headers)
    second = client.post("/v1/players", json={"id": "second-player"}, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == "first-player"
    assert second.json()["id"] == "second-player"
    assert first.json()["access_token"] != second.json()["access_token"]
    assert session.get(Player, "first-player") is not None
    assert session.get(Player, "second-player") is not None


def test_idempotency_does_not_replay_anonymous_famous_game_attempt(
    client: TestClient, session: Session
) -> None:
    first_player = Player(id="first-attempt-player")
    second_player = Player(id="second-attempt-player")
    session.add(first_player)
    session.add(second_player)
    session.add(PlayerProfile(player_id=first_player.id))
    session.add(PlayerProfile(player_id=second_player.id))
    headers = {"Idempotency-Key": "shared-attempt-key"}
    first = client.post(
        "/v1/content/famous-games/morphy-opera-1858/attempts",
        json={
            "player_id": first_player.id,
            "decision_id": "opera-open-lines",
            "hint_assisted": False,
            "response_payload": {"move": "Nxb5"},
        },
        headers=headers,
    )
    second = client.post(
        "/v1/content/famous-games/morphy-opera-1858/attempts",
        json={
            "player_id": second_player.id,
            "decision_id": "opera-open-lines",
            "hint_assisted": False,
            "response_payload": {"move": "Nxb5"},
        },
        headers=headers,
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    attempts = session.exec(select(ContentAttempt).order_by(ContentAttempt.player_id)).all()
    assert [attempt.player_id for attempt in attempts] == [first_player.id, second_player.id]
