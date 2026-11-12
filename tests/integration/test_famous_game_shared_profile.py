import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, PlayerProfile, issue_player_token
from scan64.content.models import ContentAttempt, ContentItem
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import create_db_and_tables, get_session

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    create_db_and_tables()


def _create_player_profile(player_id: str, rating: int = 1500) -> str:
    token, token_hash = issue_player_token()
    with next(get_session()) as session:
        session.add(Player(id=player_id))
        session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
        session.add(PlayerProfile(player_id=player_id, rating=rating))
        session.commit()
    return token


def test_famous_game_attempt_updates_shared_profile() -> None:
    player_id = str(uuid.uuid4())
    game_id = "morphy-opera-1858"
    token = _create_player_profile(player_id)
    attempt_data = {
        "player_id": player_id,
        "decision_id": "opera-open-lines",
        "hint_assisted": False,
        "response_payload": {"move": "Nxb5"},
    }
    response = client.post(
        f"/v1/content/famous-games/{game_id}/attempts",
        json=attempt_data,
        headers={"Authorization": f"Bearer {token}"},
    )

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
    player_id = str(uuid.uuid4())
    token = _create_player_profile(player_id)
    response = client.post(
        "/v1/content/famous-games/morphy-opera-1858/attempts",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "player_id": player_id,
            "decision_id": "opera-open-lines",
            "hint_assisted": False,
            "response_payload": {"move": "Bxf6"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is False


def test_famous_game_attempt_rejects_unknown_decision() -> None:
    player_id = str(uuid.uuid4())
    token = _create_player_profile(player_id)
    response = client.post(
        "/v1/content/famous-games/morphy-opera-1858/attempts",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "player_id": player_id,
            "decision_id": "unknown",
            "hint_assisted": False,
            "response_payload": {"move": "e4"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown famous-game decision"


@pytest.mark.parametrize("authorization", ["missing", "other", "invalid"])
@pytest.mark.parametrize("existing_skill", [False, True])
def test_famous_game_attempt_requires_owner_without_mutation(
    authorization: str, existing_skill: bool
) -> None:
    player_id = str(uuid.uuid4())
    _create_player_profile(player_id)
    other_token = _create_player_profile(str(uuid.uuid4()))
    with next(get_session()) as session:
        if existing_skill:
            session.add(
                SkillState(
                    player_id=player_id, concept_code="tactics.sacrifice", alpha=3.0, beta=2.0
                )
            )
            session.commit()
        before = [
            skill.model_dump()
            for skill in session.exec(
                select(SkillState).where(SkillState.player_id == player_id)
            ).all()
        ]
    headers = (
        {}
        if authorization == "missing"
        else {"Authorization": f"Bearer {other_token if authorization == 'other' else 'invalid'}"}
    )
    response = client.post(
        "/v1/content/famous-games/morphy-opera-1858/attempts",
        headers=headers,
        json={
            "player_id": player_id,
            "decision_id": "opera-open-lines",
            "hint_assisted": False,
            "response_payload": {"move": "Nxb5"},
        },
    )
    assert response.status_code == (404 if authorization == "other" else 401)
    if authorization == "other":
        assert response.json() == {"detail": "Player not found"}
        unknown = client.post(
            "/v1/content/famous-games/morphy-opera-1858/attempts",
            headers=headers,
            json={
                "player_id": str(uuid.uuid4()),
                "decision_id": "opera-open-lines",
                "hint_assisted": False,
                "response_payload": {"move": "Nxb5"},
            },
        )
        assert unknown.status_code == response.status_code
        assert unknown.json() == response.json()
    with next(get_session()) as session:
        assert not session.exec(
            select(ContentAttempt).where(ContentAttempt.player_id == player_id)
        ).all()
        after = [
            skill.model_dump()
            for skill in session.exec(
                select(SkillState).where(SkillState.player_id == player_id)
            ).all()
        ]
        assert after == before
