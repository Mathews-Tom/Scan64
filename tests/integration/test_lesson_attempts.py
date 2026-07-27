from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.models import Player, PlayerCredential, PlayerProfile, issue_player_token
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule


def lesson_spec() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "lesson_id": "generated-lesson",
        "source": {
            "kind": "custom",
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        },
        "diagnosis": {"primary": "tactics.fork", "confidence": 1.0},
        "objective": {"type": "find_best_move", "instruction": "Play e4."},
        "interaction": {
            "input": "click",
            "maximum_attempts": 3,
            "accepted_moves": [{"san": "e4"}],
        },
        "verification": {"status": "verified", "engine": "test"},
        "hints": [],
        "explanation": {"text": "Develop the centre."},
    }


def create_persisted_lesson(db_session: Session, player_id: str) -> PersistedLessonOpportunity:
    game = Game(pgn="", owner_player_id=player_id)
    db_session.add(game)
    db_session.flush()
    opportunity = PersistedLessonOpportunity(
        game_id=game.id,
        player_id=player_id,
        lesson_spec=lesson_spec(),
    )
    db_session.add(opportunity)
    db_session.flush()
    db_session.add(
        ReviewSchedule(
            player_id=player_id,
            item_id=str(opportunity.id),
            skill_id="tactics.fork",
            next_review_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    return opportunity


def create_study_session(db_session: Session, player_id: str) -> StudySession:
    study_session = StudySession(player_id=player_id, domain="daily_training")
    db_session.add(study_session)
    db_session.commit()
    return study_session


def authorize(client: TestClient, db_session: Session, player_id: str) -> None:
    token, token_hash = issue_player_token()
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    client.headers["Authorization"] = f"Bearer {token}"


def test_served_session_and_verified_lesson_attempt_update_profile_and_schedule(
    client: TestClient, db_session: Session
) -> None:
    player_id = "lesson-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    authorize(client, db_session, player_id)

    served = client.get(f"/v1/learning/session?player_id={player_id}")
    assert served.status_code == 200
    served_session_id = served.json()["session_id"]
    assert db_session.get(StudySession, served_session_id) is not None
    assert [lesson["lesson_id"] for lesson in served.json()["lessons"]] == [
        str(opportunity.id)
    ]

    response = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": served_session_id,
            "lesson_id": str(opportunity.id),
            "source_kind": "persisted_opportunity",
            "submitted_move": "e2e4",
            "elapsed_ms": 4200,
            "hints_used": 0,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": response.json()["id"],
        "success": True,
        "grading_status": "verified",
        "profile_update_result": "applied",
    }

    attempt = db_session.get(LessonAttempt, response.json()["id"])
    assert attempt is not None
    assert attempt.session_id == served_session_id
    assert attempt.opportunity_id == opportunity.id
    schedule = db_session.get(ReviewSchedule, (player_id, str(opportunity.id)))
    assert schedule is not None
    assert schedule.last_reviewed_at is not None
    skill = db_session.get(SkillState, (player_id, "tactics.fork"))
    assert skill is not None
    assert skill.alpha == skill.prior_alpha + 1.0


def test_served_session_without_owned_opportunities_is_explicitly_empty(
    client: TestClient, db_session: Session
) -> None:
    player_id = "no-eligible-lessons-player"
    db_session.add(Player(id=player_id))
    db_session.commit()
    authorize(client, db_session, player_id)

    response = client.get(f"/v1/learning/session?player_id={player_id}")

    assert response.status_code == 200
    assert response.json()["lessons"] == []
    assert db_session.get(StudySession, response.json()["session_id"]) is not None

def test_training_session_rejects_missing_and_wrong_player_tokens(
    client: TestClient, db_session: Session
) -> None:
    owner_id = "protected-training-owner"
    other_player_id = "wrong-training-player"
    db_session.add(Player(id=owner_id))
    db_session.add(Player(id=other_player_id))
    db_session.commit()

    authorize(client, db_session, owner_id)
    client.headers.pop("Authorization")
    missing_token_response = client.get(f"/v1/learning/session?player_id={owner_id}")

    authorize(client, db_session, other_player_id)
    wrong_token_response = client.get(f"/v1/learning/session?player_id={owner_id}")

    assert missing_token_response.status_code == 401
    assert wrong_token_response.status_code == 403
    assert db_session.exec(
        select(StudySession).where(StudySession.player_id == owner_id)
    ).all() == []



def test_persisted_attempt_rejects_static_or_unowned_lesson_ids(
    client: TestClient, db_session: Session
) -> None:
    player_id = "attempt-owner"
    other_player_id = "other-attempt-owner"
    db_session.add(Player(id=player_id))
    db_session.add(Player(id=other_player_id))
    db_session.commit()
    authorize(client, db_session, player_id)
    study_session = create_study_session(db_session, player_id)
    other_opportunity = create_persisted_lesson(db_session, other_player_id)

    static_id_response = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": study_session.id,
            "lesson_id": "morphy-opera-1858_opera-open-lines",
            "source_kind": "persisted_opportunity",
            "submitted_move": "e2e4",
            "elapsed_ms": 1,
            "hints_used": 0,
        },
    )
    unowned_id_response = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": study_session.id,
            "lesson_id": str(other_opportunity.id),
            "source_kind": "persisted_opportunity",
            "submitted_move": "e2e4",
            "elapsed_ms": 1,
            "hints_used": 0,
        },
    )

    assert static_id_response.status_code == 422
    assert unowned_id_response.status_code == 404

def test_lesson_attempt_rejects_missing_and_wrong_player_tokens(
    client: TestClient, db_session: Session
) -> None:
    owner_id = "protected-attempt-owner"
    other_player_id = "wrong-attempt-player"
    db_session.add(Player(id=owner_id))
    db_session.add(Player(id=other_player_id))
    db_session.commit()
    study_session = create_study_session(db_session, owner_id)
    attempt_payload = {
        "session_id": study_session.id,
        "lesson_id": "not-reached-without-an-owner-token",
        "source_kind": "persisted_opportunity",
        "submitted_move": "e2e4",
        "elapsed_ms": 1,
        "hints_used": 0,
    }

    authorize(client, db_session, owner_id)
    client.headers.pop("Authorization")
    missing_token_response = client.post(
        "/v1/learning/lesson-attempts", json=attempt_payload
    )

    authorize(client, db_session, other_player_id)
    wrong_token_response = client.post(
        "/v1/learning/lesson-attempts", json=attempt_payload
    )

    assert missing_token_response.status_code == 401
    assert wrong_token_response.status_code == 403
    assert db_session.exec(
        select(LessonAttempt).where(LessonAttempt.session_id == study_session.id)
    ).all() == []

def test_wrong_attempt_is_recorded_and_reveals_failure_state(
    client: TestClient, db_session: Session
) -> None:
    player_id = "wrong-move-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    study_session = create_study_session(db_session, player_id)
    authorize(client, db_session, player_id)

    response = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": study_session.id,
            "lesson_id": str(opportunity.id),
            "source_kind": "persisted_opportunity",
            "submitted_move": "d2d4",
            "elapsed_ms": 900,
            "hints_used": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is False
    attempt = db_session.get(LessonAttempt, response.json()["id"])
    assert attempt is not None
    assert attempt.success is False
    skill = db_session.get(SkillState, (player_id, "tactics.fork"))
    assert skill is not None
    assert skill.beta == skill.prior_beta + 1.0


def test_retired_skill_attempt_is_retained_without_reactivation(
    client: TestClient, db_session: Session
) -> None:
    player_id = "retired-player"
    now = datetime.now(UTC)
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.add(
        SkillState(
            player_id=player_id,
            concept_code="tactics.fork",
            retired_at=now,
            retirement_reason="taxonomy retired",
        )
    )
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    schedule = db_session.get(ReviewSchedule, (player_id, str(opportunity.id)))
    assert schedule is not None
    schedule.retired_at = now
    schedule.retirement_reason = "taxonomy retired"
    db_session.add(schedule)
    db_session.commit()
    study_session = create_study_session(db_session, player_id)
    authorize(client, db_session, player_id)

    response = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": study_session.id,
            "lesson_id": str(opportunity.id),
            "source_kind": "persisted_opportunity",
            "submitted_move": "e2e4",
            "elapsed_ms": 900,
            "hints_used": 0,
        },
    )
    assert response.status_code == 200
    assert response.json()["profile_update_result"] == "skipped_retired"
    retired_skill = db_session.get(SkillState, (player_id, "tactics.fork"))
    assert retired_skill is not None
    assert retired_skill.retired_at is not None


def test_opening_mission_is_recorded_as_ungraded_attempt(
    client: TestClient, db_session: Session
) -> None:
    player_id = "opening-player"
    db_session.add(Player(id=player_id))
    db_session.commit()
    authorize(client, db_session, player_id)
    study_session = create_study_session(db_session, player_id)

    response = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": study_session.id,
            "lesson_id": "opening-mission:italian:minor-pieces",
            "source_kind": "opening_mission",
            "submitted_move": "e2e4",
            "elapsed_ms": 500,
            "hints_used": 0,
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is None
    assert response.json()["grading_status"] == "ungraded"
    attempt = db_session.get(LessonAttempt, response.json()["id"])
    assert attempt is not None
    assert attempt.profile_update_result == "not_applicable"
    assert db_session.exec(select(SkillState).where(SkillState.player_id == player_id)).all() == []
