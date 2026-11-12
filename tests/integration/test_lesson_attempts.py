from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.api.learning as learning_api
from scan64.api.models import Player, PlayerCredential, PlayerProfile, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.content.transfer_catalog import seed_transfer_positions
from scan64.learning.evaluation.transfer_measurement import (
    MeasurementPoint,
    TransferMeasurement,
    assign_production_transfer_measurements,
    due_transfer_measurements,
)
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.transfer import TransferKind
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
        "diagnosis": {"primary": "threat_processing.missed_capture", "confidence": 1.0},
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
    source_position = Position(
        game_id=game.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial",
    )
    db_session.add(source_position)
    db_session.flush()
    evidence_id = f"lesson-proof-{source_position.id}"
    spec = lesson_spec()
    spec["diagnosis"] = {
        "primary": "threat_processing.missed_capture",
        "confidence": 1.0,
        "evidence_refs": [evidence_id],
    }
    db_session.add(
        Evidence(
            evidence_id=evidence_id,
            kind="missed_opportunity",
            position_id=str(source_position.id),
            engine_analysis_id="lesson-analysis",
            claim="Lesson-serving diagnosis fixture",
            payload={"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION},
        )
    )
    opportunity = PersistedLessonOpportunity(
        game_id=game.id,
        source_position_id=source_position.id,
        player_id=player_id,
        lesson_spec=spec,
    )
    db_session.add(opportunity)
    db_session.flush()
    db_session.add(
        ReviewSchedule(
            player_id=player_id,
            item_id=str(opportunity.id),
            skill_id="threat_processing.missed_capture",
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
    assert [lesson["lesson_id"] for lesson in served.json()["lessons"]] == [str(opportunity.id)]

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
    skill = db_session.get(SkillState, (player_id, "threat_processing.missed_capture"))
    assert skill is not None
    assert skill.alpha == skill.prior_alpha + 1.0


def test_game_learning_opportunity_serves_owned_context_for_verified_attempt(
    client: TestClient, db_session: Session
) -> None:
    player_id = "game-learning-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    authorize(client, db_session, player_id)

    served = client.get(
        f"/v1/games/{opportunity.game_id}/learning-opportunities?player_id={player_id}"
    )

    assert served.status_code == 200
    served_session_id = served.json()["session_id"]
    assert [lesson["lesson_id"] for lesson in served.json()["lessons"]] == [str(opportunity.id)]
    study_session = db_session.get(StudySession, served_session_id)
    assert study_session is not None
    assert study_session.player_id == player_id
    assert study_session.domain == f"game_analysis:{opportunity.game_id}"
    repeated = client.get(
        f"/v1/games/{opportunity.game_id}/learning-opportunities?player_id={player_id}"
    )
    assert repeated.status_code == 200
    assert repeated.json()["session_id"] == served_session_id

    study_sessions = db_session.exec(
        select(StudySession).where(StudySession.player_id == player_id)
    ).all()
    assert [session.id for session in study_sessions] == [served_session_id]
    recorded = client.post(
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

    assert recorded.status_code == 200
    assert recorded.json()["grading_status"] == "verified"
    attempt = db_session.get(LessonAttempt, recorded.json()["id"])
    assert attempt is not None
    assert attempt.session_id == served_session_id
    assert attempt.opportunity_id == opportunity.id


def test_game_learning_opportunities_require_authentication_before_ownership(
    client: TestClient, db_session: Session
) -> None:
    owner_id = "protected-game-owner"
    other_player_id = "other-game-player"
    db_session.add(Player(id=owner_id))
    db_session.add(Player(id=other_player_id))
    db_session.add(PlayerProfile(player_id=owner_id, rating=1500))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, owner_id)

    missing_token_response = client.get(
        f"/v1/games/{opportunity.game_id}/learning-opportunities?player_id={owner_id}"
    )

    authorize(client, db_session, other_player_id)
    wrong_token_response = client.get(
        f"/v1/games/{opportunity.game_id}/learning-opportunities?player_id={owner_id}"
    )
    unowned_game_response = client.get(
        f"/v1/games/{opportunity.game_id}/learning-opportunities?player_id={other_player_id}"
    )

    assert missing_token_response.status_code == 401
    assert wrong_token_response.status_code == 403
    assert unowned_game_response.status_code == 404
    assert (
        db_session.exec(select(StudySession).where(StudySession.player_id == owner_id)).all() == []
    )


def test_empty_game_learning_opportunities_do_not_create_a_study_session(
    client: TestClient, db_session: Session
) -> None:
    player_id = "empty-game-learning-player"
    db_session.add(Player(id=player_id))
    game = Game(pgn="", owner_player_id=player_id)
    db_session.add(game)
    db_session.commit()
    authorize(client, db_session, player_id)

    response = client.get(f"/v1/games/{game.id}/learning-opportunities?player_id={player_id}")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": None,
        "lessons": [],
        "next_cursor": None,
    }
    assert (
        db_session.exec(select(StudySession).where(StudySession.player_id == player_id)).all() == []
    )


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
    assert (
        db_session.exec(select(StudySession).where(StudySession.player_id == owner_id)).all() == []
    )


def test_scheduleless_opportunities_are_not_served(client: TestClient, db_session: Session) -> None:
    player_id = "scheduleless-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
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
        PersistedLessonOpportunity(
            game_id=game.id,
            source_position_id=source_position.id,
            player_id=player_id,
            lesson_spec=lesson_spec(),
        )
    )
    db_session.commit()
    authorize(client, db_session, player_id)

    daily_response = client.get(f"/v1/learning/session?player_id={player_id}")
    game_response = client.get(f"/v1/games/{game.id}/learning-opportunities?player_id={player_id}")

    assert daily_response.status_code == 200
    assert daily_response.json()["lessons"] == []
    assert db_session.get(StudySession, daily_response.json()["session_id"]) is not None
    assert game_response.status_code == 200
    assert game_response.json()["lessons"] == []
    assert game_response.json()["session_id"] is None


def test_persisted_attempt_records_a_legal_format_illegal_move(
    client: TestClient, db_session: Session
) -> None:
    player_id = "illegal-move-player"
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
            "submitted_move": "a3a4",
            "elapsed_ms": 1,
            "hints_used": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    attempts = db_session.exec(
        select(LessonAttempt).where(LessonAttempt.session_id == study_session.id)
    ).all()
    assert len(attempts) == 1
    assert attempts[0].success is False


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
    removed_source_response = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": study_session.id,
            "lesson_id": str(other_opportunity.id),
            "source_kind": "critical_moment",
            "submitted_move": "e2e4",
            "elapsed_ms": 1,
            "hints_used": 0,
        },
    )

    assert static_id_response.status_code == 422
    assert unowned_id_response.status_code == 404
    assert removed_source_response.status_code == 422


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
    missing_token_response = client.post("/v1/learning/lesson-attempts", json=attempt_payload)
    unknown_session_response = client.post(
        "/v1/learning/lesson-attempts",
        json={**attempt_payload, "session_id": "unknown-study-session"},
        headers={"Authorization": "Bearer unregistered-token"},
    )

    authorize(client, db_session, other_player_id)
    wrong_token_response = client.post("/v1/learning/lesson-attempts", json=attempt_payload)

    assert missing_token_response.status_code == 401
    assert unknown_session_response.status_code == 401
    assert wrong_token_response.status_code == 404
    assert (
        db_session.exec(
            select(LessonAttempt).where(LessonAttempt.session_id == study_session.id)
        ).all()
        == []
    )


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

    attempt_payload = {
        "session_id": study_session.id,
        "lesson_id": str(opportunity.id),
        "source_kind": "persisted_opportunity",
        "submitted_move": "d2d4",
        "elapsed_ms": 900,
        "hints_used": 1,
    }
    for _ in range(3):
        response = client.post("/v1/learning/lesson-attempts", json=attempt_payload)
        assert response.status_code == 200
        assert response.json()["success"] is False

    exhausted_response = client.post("/v1/learning/lesson-attempts", json=attempt_payload)
    assert exhausted_response.status_code == 409
    reloaded_study_session = create_study_session(db_session, player_id)
    reloaded_payload = {**attempt_payload, "session_id": reloaded_study_session.id}
    assert client.post("/v1/learning/lesson-attempts", json=reloaded_payload).status_code == 409
    attempts = db_session.exec(
        select(LessonAttempt)
        .where(LessonAttempt.session_id == study_session.id)
        .where(LessonAttempt.opportunity_id == opportunity.id)
    ).all()
    assert len(attempts) == 3
    assert all(attempt.success is False for attempt in attempts)
    skill = db_session.get(SkillState, (player_id, "threat_processing.missed_capture"))
    assert skill is not None
    assert skill.beta == pytest.approx(skill.prior_beta + 3.0)


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
            concept_code="threat_processing.missed_capture",
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
    retired_skill = db_session.get(SkillState, (player_id, "threat_processing.missed_capture"))
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


def test_transfer_measurement_is_served_completed_and_reported(
    client: TestClient, db_session: Session
) -> None:
    player_id = "transfer-player"
    skill_id = "tactics.pin"
    now = datetime.now(UTC)
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1300))
    db_session.commit()
    seed_transfer_positions(db_session)
    assign_production_transfer_measurements(
        db_session,
        player_id=player_id,
        skill_id=skill_id,
        target_difficulty=1300,
        now=now,
    )
    measurement = due_transfer_measurements(db_session, player_id=player_id, now=now)[0]
    assert measurement.target_move_uci is not None
    authorize(client, db_session, player_id)

    served = client.get(f"/v1/learning/session?player_id={player_id}")

    assert served.status_code == 200
    assert str(measurement.id) in {lesson["lesson_id"] for lesson in served.json()["lessons"]}
    completed = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": served.json()["session_id"],
            "lesson_id": str(measurement.id),
            "source_kind": "transfer_measurement",
            "submitted_move": measurement.target_move_uci,
            "elapsed_ms": 1000,
            "hints_used": 0,
        },
    )

    assert completed.status_code == 200
    assert completed.json()["success"] is True
    post_tests = list(
        db_session.exec(
            select(TransferMeasurement)
            .where(TransferMeasurement.player_id == player_id)
            .where(TransferMeasurement.skill_id == skill_id)
            .where(TransferMeasurement.measurement_point != MeasurementPoint.PRE_TEST)
        )
    )
    assert len(post_tests) == 2
    assert all(post_test.scheduled_for is not None for post_test in post_tests)
    assert (
        len(due_transfer_measurements(db_session, player_id=player_id, now=datetime.now(UTC))) == 1
    )
    repeated = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": served.json()["session_id"],
            "lesson_id": str(measurement.id),
            "source_kind": "transfer_measurement",
            "submitted_move": measurement.target_move_uci,
            "elapsed_ms": 1000,
            "hints_used": 0,
        },
    )
    assert repeated.status_code == 409
    report = client.get(f"/v1/learning/transfer-report?player_id={player_id}&skill_id={skill_id}")
    assert report.status_code == 200
    pre_test = next(
        item for item in report.json()["measurements"] if item["measurement_point"] == "pre_test"
    )
    assert pre_test == {
        "measurement_point": "pre_test",
        "assigned_count": 1,
        "completed_count": 1,
        "successful_count": 1,
        "success_rate": 1.0,
    }


def test_mastered_lesson_without_catalog_positions_still_records_attempt(
    client: TestClient, db_session: Session
) -> None:
    player_id = "mastery-player"
    skill_id = "tactics.overloaded_defender"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1300))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    schedule = db_session.get(ReviewSchedule, (player_id, str(opportunity.id)))
    assert schedule is not None
    schedule.skill_id = skill_id
    opportunity.verification_status = "verified"
    db_session.add(
        SkillState(
            player_id=player_id,
            concept_code=skill_id,
            alpha=8.0,
            beta=1.0,
        )
    )
    db_session.add_all((schedule, opportunity))
    db_session.commit()
    authorize(client, db_session, player_id)
    study_session = create_study_session(db_session, player_id)

    recorded = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": study_session.id,
            "lesson_id": str(opportunity.id),
            "source_kind": "persisted_opportunity",
            "submitted_move": "e2e4",
            "elapsed_ms": 1000,
            "hints_used": 0,
        },
    )

    assert recorded.status_code == 200
    assert db_session.get(LessonAttempt, recorded.json()["id"]) is not None


def test_session_limits_due_transfer_measurements_to_session_capacity(
    client: TestClient, db_session: Session
) -> None:
    player_id = "transfer-capacity-player"
    now = datetime.now(UTC)
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1300))
    db_session.commit()
    seed_transfer_positions(db_session)
    assigned = (
        *assign_production_transfer_measurements(
            db_session,
            player_id=player_id,
            skill_id="tactics.fork.knight",
            target_difficulty=1300,
            now=now,
        ),
        *assign_production_transfer_measurements(
            db_session,
            player_id=player_id,
            skill_id="tactics.pin",
            target_difficulty=1300,
            now=now,
        ),
    )
    for measurement in assigned:
        measurement.scheduled_for = now
        if db_session.get(ReviewSchedule, (player_id, measurement.id)) is None:
            db_session.add(
                ReviewSchedule(
                    player_id=player_id,
                    item_id=measurement.id,
                    next_review_at=now,
                )
            )
    legacy_measurement = TransferMeasurement(
        id="legacy-transfer-slot",
        cohort_id="legacy-cohort",
        player_id=player_id,
        skill_id="tactics.pin",
        measurement_point=MeasurementPoint.PRE_TEST,
        source_position_id="legacy-source",
        source_fen="4k3/8/2n5/1B6/8/8/P6P/7K w - - 0 1",
        target_fen="4k3/8/2n5/1B6/8/8/P6P/7K w - - 0 1",
        transfer_kind=TransferKind.NEAR,
        scheduled_for=datetime(2020, 1, 1, tzinfo=UTC),
    )
    db_session.add(legacy_measurement)
    db_session.add(
        ReviewSchedule(
            player_id=player_id,
            item_id=legacy_measurement.id,
            next_review_at=legacy_measurement.scheduled_for,
        )
    )
    db_session.commit()
    authorize(client, db_session, player_id)

    served = client.get(f"/v1/learning/session?player_id={player_id}")

    assert served.status_code == 200
    assert len(served.json()["lessons"]) == 5


def test_session_skips_unplayable_legacy_transfer_measurements(
    client: TestClient, db_session: Session
) -> None:
    player_id = "legacy-transfer-player"
    measurement_id = "legacy-transfer-measurement"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1300))
    db_session.add(
        TransferMeasurement(
            id=measurement_id,
            cohort_id="legacy-cohort",
            player_id=player_id,
            skill_id="tactics.pin",
            measurement_point=MeasurementPoint.PRE_TEST,
            source_position_id="legacy-source",
            source_fen="4k3/8/2n5/1B6/8/8/P6P/7K w - - 0 1",
            target_fen="4k3/8/2n5/1B6/8/8/P6P/7K w - - 0 1",
            transfer_kind=TransferKind.NEAR,
            scheduled_for=datetime.now(UTC),
        )
    )
    db_session.add(
        ReviewSchedule(
            player_id=player_id,
            item_id=measurement_id,
            next_review_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    authorize(client, db_session, player_id)

    served = client.get(f"/v1/learning/session?player_id={player_id}")
    missing_report = client.get(
        f"/v1/learning/transfer-report?player_id={player_id}&skill_id=tactics.fork"
    )

    assert served.status_code == 200
    assert served.json()["lessons"] == []
    assert db_session.get(TransferMeasurement, measurement_id) is not None
    assert missing_report.status_code == 404


@pytest.mark.parametrize("fallback", [False, True])
def test_non_best_sound_attempt_records_success_and_updates_profile(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, fallback: bool
) -> None:
    player_id = "sound-alternative-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    recorded = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "m47_source_multipv.json").read_text()
    )
    source = db_session.get(Position, opportunity.source_position_id)
    assert source is not None
    source.fen = recorded["fen"]
    source.side_to_move = "b"
    source.canonical_id = chess.Board(recorded["fen"]).epd()
    db_session.add(source)
    calls: list[int] = []

    async def source_analysis(_self: object, fen: str, **limits: int) -> EngineAnalysis:
        assert fen == recorded["fen"]
        assert limits["nodes"] == 100_000
        calls.append(limits["multipv"])
        return EngineAnalysis(
            position_id=source.id,
            raw_result=recorded["searches"][str(limits["multipv"])]["raw_result"],
        )

    monkeypatch.setattr(learning_api.StockfishAdapter, "analyze_position", source_analysis)
    if not fallback:
        analysis = EngineAnalysis(
            position_id=source.id, raw_result=recorded["searches"]["4"]["raw_result"]
        )
        db_session.add(analysis)
        opportunity.verification_analysis_id = analysis.id
    spec = dict(opportunity.lesson_spec)
    spec["source"] = {"kind": "player_game", "fen": recorded["fen"]}
    spec["interaction"] = {
        "input": "move",
        "maximum_attempts": 3,
        "accepted_moves": [{"san": "Na5"}, {"san": "O-O-O"}],
    }
    opportunity.lesson_spec = spec
    db_session.add(opportunity)
    db_session.commit()
    authorize(client, db_session, player_id)

    served = client.get(f"/v1/learning/session?player_id={player_id}")
    assert served.status_code == 200
    assert [
        move["san"] for move in served.json()["lessons"][0]["interaction"]["accepted_moves"]
    ] == ["Na5", "O-O-O"]
    assert calls == ([2, 4] if fallback else [])
    response = client.post(
        "/v1/learning/lesson-attempts",
        json={
            "session_id": served.json()["session_id"],
            "lesson_id": str(opportunity.id),
            "source_kind": "persisted_opportunity",
            "submitted_move": "e8c8",
            "elapsed_ms": 4200,
            "hints_used": 0,
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["profile_update_result"] == "applied"
    attempt = db_session.get(LessonAttempt, response.json()["id"])
    assert attempt is not None and attempt.success is True
    skill = db_session.get(SkillState, (player_id, "threat_processing.missed_capture"))
    assert skill is not None and skill.alpha == skill.prior_alpha + 1


@pytest.mark.parametrize(("accepted_move", "scored"), [("e4", False), ("e4", True), ("d4", True)])
def test_pre_m47_single_move_lesson_verifies_on_read(
    client: TestClient, db_session: Session, accepted_move: str, scored: bool
) -> None:
    player_id = "legacy-sound-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    analysis = EngineAnalysis(
        position_id=opportunity.source_position_id,
        raw_result=[
            {"pv": ["e2e4"], "score_cp": 100},
            {"pv": ["d2d4"], "score_cp": 50},
            {"pv": ["a2a3"], "score_cp": -300},
        ],
    )
    if not scored:
        analysis.raw_result = [{"pv": ["e2e4"]}]
    db_session.add(analysis)
    opportunity.verification_analysis_id = analysis.id
    spec = dict(opportunity.lesson_spec)
    spec["interaction"] = {
        "input": "move",
        "maximum_attempts": 3,
        "accepted_moves": [{"san": accepted_move}],
    }
    opportunity.lesson_spec = spec
    db_session.add(opportunity)
    db_session.commit()
    authorize(client, db_session, player_id)

    served = client.get(f"/v1/learning/session?player_id={player_id}")
    assert served.status_code == 200
    lessons = served.json()["lessons"]
    assert len(lessons) == 1
    assert lessons[0]["verification"]["status"] == "verified"
    assert [move["san"] for move in lessons[0]["interaction"]["accepted_moves"]] == [accepted_move]
    db_session.refresh(opportunity)
    assert opportunity.verification_status == "verified"


@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.parametrize("screen", ["training", "critical"])
@pytest.mark.parametrize("name", ["C", "D"])
def test_legacy_lesson_receives_and_persists_the_same_ladder_without_engine_search(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    verified: bool,
    screen: str,
    name: str,
) -> None:
    from chess_lesson_spec import LessonSpec

    from scan64.learning.exercises.hints import fill_lesson_hints

    player_id = "legacy-hint-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    record = json.loads(
        (Path(__file__).parents[1] / "fixtures/hint_engine_recording.json").read_text()
    )["positions"][name]
    source = db_session.get(Position, opportunity.source_position_id)
    assert source is not None
    source.fen = record["fen"]
    spec = LessonSpec.model_validate(opportunity.lesson_spec)
    spec.source.fen = source.fen
    spec.interaction.accepted_moves[0].san = "Nxc7+" if name == "C" else "h3"
    opportunity.lesson_spec = spec.model_dump(mode="json")
    opportunity.verification_status = "verified" if verified else "unverified"
    analysis = EngineAnalysis(position_id=source.id, raw_result=record["raw_result"])
    db_session.add(analysis)
    opportunity.verification_analysis_id = analysis.id
    db_session.add_all((source, opportunity))
    db_session.commit()
    authorize(client, db_session, player_id)

    async def forbidden_search(*_args, **_kwargs):
        pytest.fail("Legacy ladder must reuse persisted source analysis")

    monkeypatch.setattr(learning_api.StockfishAdapter, "analyze_position", forbidden_search)
    path = (
        f"/v1/learning/session?player_id={player_id}"
        if screen == "training"
        else f"/v1/games/{opportunity.game_id}/learning-opportunities?player_id={player_id}"
    )
    served = client.get(path)
    assert served.status_code == 200, served.text
    lesson = served.json()["lessons"][0]
    fill_lesson_hints(spec, analysis)
    assert lesson["hints"] == spec.model_dump(mode="json")["hints"]
    assert lesson["explanation"] == spec.model_dump(mode="json")["explanation"]
    nudge = lesson["hints"][0]
    assert len(nudge["visualizations"]) == 1
    assert all(drawing["command"] == "highlight_square" for drawing in nudge["visualizations"])
    squares = {drawing["square"] for drawing in nudge["visualizations"]}
    board = chess.Board(source.fen)
    for accepted_move in spec.interaction.accepted_moves:
        move = board.parse_san(accepted_move.san).uci()
        assert squares.isdisjoint({move[:2], move[2:4]})
    assert squares.isdisjoint({lesson["hints"][1]["visualizations"][0]["square"]})
    db_session.refresh(opportunity)
    assert opportunity.lesson_spec["hints"] == lesson["hints"]
    assert client.get(path).json()["lessons"][0]["hints"] == lesson["hints"]


@pytest.mark.parametrize("screen", ["training", "critical"])
@pytest.mark.parametrize("invalid_analysis", [False, True])
def test_legacy_hint_read_limits_engine_fallback_to_one_lesson(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    screen: str,
    invalid_analysis: bool,
) -> None:
    player_id = "bounded-hint-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    first = create_persisted_lesson(db_session, player_id)
    second = create_persisted_lesson(db_session, player_id)
    second.game_id = first.game_id
    db_session.add(second)
    db_session.commit()
    calls = []

    async def recorded_search(_self, fen, **limits):
        calls.append(limits)
        assert fen == chess.STARTING_FEN
        return EngineAnalysis(
            raw_result=[] if invalid_analysis else [{"pv": ["e2e4"], "score_cp": 0}]
        )

    monkeypatch.setattr(learning_api.StockfishAdapter, "analyze_position", recorded_search)
    authorize(client, db_session, player_id)
    path = (
        f"/v1/learning/session?player_id={player_id}"
        if screen == "training"
        else f"/v1/games/{first.game_id}/learning-opportunities?player_id={player_id}"
    )
    served = client.get(path)
    assert served.status_code == 200, served.text
    lessons = served.json()["lessons"]
    assert len(lessons) == (0 if invalid_analysis else 1)
    if lessons:
        assert len(lessons[0]["hints"]) == 3
    assert calls == [{"nodes": 100_000, "multipv": 2}]


@pytest.mark.parametrize("screen", ["training", "critical"])
def test_invalid_source_analysis_consumes_budget_and_is_not_researched_on_read(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    screen: str,
) -> None:
    player_id = "invalid-source-hint-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    first = create_persisted_lesson(db_session, player_id)
    calls = []

    async def recorded_search(_self, fen, **limits):
        calls.append(limits)
        assert fen == chess.STARTING_FEN
        return EngineAnalysis(raw_result=[])

    monkeypatch.setattr(learning_api.StockfishAdapter, "analyze_position", recorded_search)
    authorize(client, db_session, player_id)
    path = (
        f"/v1/learning/session?player_id={player_id}"
        if screen == "training"
        else f"/v1/games/{first.game_id}/learning-opportunities?player_id={player_id}"
    )
    served = client.get(path)
    assert served.status_code == 200, served.text
    assert served.json()["lessons"] == []
    assert calls == [{"nodes": 100_000, "multipv": 2}]
    invalid = first
    db_session.refresh(invalid)
    assert invalid.verification_status == "invalid"
    assert invalid.verification_error == "Engine analysis has no principal variation"
    assert invalid.verification_analysis_id is None
    again = client.get(path)
    assert again.status_code == 200, again.text
    assert again.json()["lessons"] == []
    assert calls == [{"nodes": 100_000, "multipv": 2}]


def test_correct_final_attempt_with_hints_keeps_half_credit_and_server_limit(
    client: TestClient,
    db_session: Session,
) -> None:
    player_id = "hint-assisted-player"
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=1500))
    db_session.commit()
    opportunity = create_persisted_lesson(db_session, player_id)
    analysis = EngineAnalysis(
        position_id=opportunity.source_position_id,
        raw_result=[{"pv": ["e2e4"], "score_cp": 0}],
    )
    db_session.add(analysis)
    opportunity.verification_analysis_id = analysis.id
    db_session.add(opportunity)
    db_session.commit()
    authorize(client, db_session, player_id)
    served = client.get(f"/v1/learning/session?player_id={player_id}").json()
    for index, move in enumerate(("a2a3", "h2h3", "e2e4")):
        result = client.post(
            "/v1/learning/lesson-attempts",
            json={
                "session_id": served["session_id"],
                "lesson_id": str(opportunity.id),
                "source_kind": "persisted_opportunity",
                "submitted_move": move,
                "elapsed_ms": 1000,
                "hints_used": (0, 1, 3)[index],
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["success"] is (index == 2)
    skill = db_session.get(SkillState, (player_id, "threat_processing.missed_capture"))
    assert skill is not None
    assert skill.alpha == pytest.approx(skill.prior_alpha + 0.5)
    assert skill.beta == pytest.approx(skill.prior_beta + 2)
    attempt = db_session.get(LessonAttempt, result.json()["id"])
    assert attempt is not None and attempt.hints_used == 3 and attempt.success
    assert (
        client.post(
            "/v1/learning/lesson-attempts",
            json={
                "session_id": served["session_id"],
                "lesson_id": str(opportunity.id),
                "source_kind": "persisted_opportunity",
                "submitted_move": "e2e4",
                "elapsed_ms": 1000,
                "hints_used": 3,
            },
        ).status_code
        == 409
    )
