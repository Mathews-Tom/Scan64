import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from scan64.api.app import app
from scan64.content.models import ContentAttempt, ContentItem
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import create_db_and_tables, get_session

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    create_db_and_tables()


def test_famous_game_attempt_updates_shared_profile() -> None:
    player_id = str(uuid.uuid4())
    game_id = "morphy-opera-1858"
    attempt_data = {
        "player_id": player_id,
        "decision_id": "opera-open-lines",
        "hint_assisted": False,
        "response_payload": {"move": "Nxb5"},
    }

    response = client.post(f"/v1/content/famous-games/{game_id}/attempts", json=attempt_data)

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True

    with next(get_session()) as session:
        item = session.get(ContentItem, game_id)
        assert item is not None
        assert item.domain == "famous_games"
        assert item.payload["decisions"][0]["id"] == "opera-open-lines"

        skills = session.exec(select(SkillState).where(SkillState.player_id == player_id)).all()
        skill_codes = {skill.concept_code for skill in skills}
        assert {"tactics.sacrifice", "tactics.development"} <= skill_codes

        attempts = session.exec(
            select(ContentAttempt).where(ContentAttempt.player_id == player_id)
        ).all()
        assert len(attempts) == 1
        assert attempts[0].item_id == game_id
        assert attempts[0].response_payload["decision_id"] == "opera-open-lines"


def test_famous_game_attempt_grades_the_submitted_move() -> None:
    response = client.post(
        "/v1/content/famous-games/morphy-opera-1858/attempts",
        json={
            "player_id": str(uuid.uuid4()),
            "decision_id": "opera-open-lines",
            "hint_assisted": False,
            "response_payload": {"move": "Bxf6"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is False


def test_famous_game_attempt_rejects_unknown_decision() -> None:
    response = client.post(
        "/v1/content/famous-games/morphy-opera-1858/attempts",
        json={
            "player_id": str(uuid.uuid4()),
            "decision_id": "unknown",
            "hint_assisted": False,
            "response_payload": {"move": "e4"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown famous-game decision"
