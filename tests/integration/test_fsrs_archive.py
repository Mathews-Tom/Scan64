from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.models import Player
from scan64.content.models import LessonAttempt
from scan64.learning.evaluation.transfer_measurement import TransferMeasurement
from scan64.learning.scheduling.spaced_repetition import (
    ReviewSchedule,
    ScheduleReviewObservation,
)
from scan64.learning.sparring import SparringAttempt, source_item_id
from tests.integration.test_data_lifecycle_completeness import (
    _authorization,
    _create_player_token,
    _seed_player_lifecycle_records,
)
from tests.integration.test_puzzle_lifecycle import seed_puzzle_run
from tests.integration.test_sparring_lifecycle import seed_graph

OWNER = "fsrs-archive-owner"


def _archive(client: TestClient, session: Session) -> tuple[dict[str, Any], dict[str, str]]:
    session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    headers = _authorization(_create_player_token(client, OWNER))
    run = seed_puzzle_run(session, OWNER, solved=True)
    assert run.completed_at is not None
    schedule = ReviewSchedule(
        player_id=OWNER,
        item_id=run.item_id,
        next_review_at=run.started_at,
        legacy_interval_days=3.75,
        legacy_ease_factor=2.7,
    )
    observation = schedule.update(
        success=True,
        current_time=run.completed_at,
        hints_used=0,
        elapsed_ms=0,
        source_kind="puzzle",
        source_id=run.id,
    )
    schedule.retired_at = run.completed_at
    schedule.retirement_reason = "puzzle_review_passed"
    session.add(schedule)
    session.flush()
    session.add(observation)
    session.commit()
    response = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers)
    assert response.status_code == 200, response.text
    archive = response.json()
    _delete(client, headers)
    session.expire_all()
    return archive, headers


def _delete(client: TestClient, headers: dict[str, str]) -> None:
    response = client.request(
        "DELETE",
        f"/v1/players/{OWNER}/data",
        json={"dry_run": False, "confirmation": f"delete-{OWNER}"},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _assert_rejected(
    client: TestClient,
    session: Session,
    archive: dict[str, Any],
    headers: dict[str, str],
) -> None:
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    session.expire_all()
    assert session.get(Player, OWNER) is None
    assert session.exec(select(ReviewSchedule).where(ReviewSchedule.player_id == OWNER)).all() == []
    assert (
        session.exec(
            select(ScheduleReviewObservation).where(ScheduleReviewObservation.player_id == OWNER)
        ).all()
        == []
    )


def test_modern_archive_restores_state_without_replaying_reviews(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _archive(client, db_session)
    assert archive["review_schedules"][0]["fsrs_state"] == 2
    assert archive["review_schedules"][0]["fsrs_step"] is None
    observation = archive["schedule_review_observations"][0]
    assert observation["verification_status"] == "verified"
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers).json()
    assert restored["review_schedules"] == archive["review_schedules"]
    assert restored["puzzle_rating"] == archive["puzzle_rating"]
    assert restored["puzzle_rating_events"] == archive["puzzle_rating_events"]
    assert restored["habit_credits"] == archive["habit_credits"]
    assert restored["pattern_awards"] == archive["pattern_awards"]
    assert restored["schedule_review_observations"] == [
        {**observation, "verification_status": "historical"}
    ]
    _delete(client, headers)
    response = client.post("/v1/imports", json=restored, headers=headers)
    assert response.status_code == 200, response.text
    again = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers).json()
    assert again["review_schedules"] == restored["review_schedules"]
    assert again["schedule_review_observations"] == restored["schedule_review_observations"]


def test_pre_fsrs_archive_preserves_due_retirement_and_unknown_history(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _archive(client, db_session)
    archive.pop("schedule_review_observations")
    legacy = {
        "player_id": OWNER,
        "item_id": "legacy-due",
        "next_review_at": "2026-01-02T03:04:05.123456",
        "last_reviewed_at": "2025-12-25T03:04:05.654321",
        "interval_days": 7.25,
        "ease_factor": 2.65,
        "skill_id": "tactics.hanging_piece",
        "retired_at": None,
        "retirement_reason": None,
    }
    retired = {
        **legacy,
        "item_id": "legacy-retired",
        "retired_at": "2026-01-03T03:04:05.000001",
        "retirement_reason": "legacy_completed",
    }
    archive["review_schedules"] = [legacy, retired]
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers).json()
    assert restored["schedule_review_observations"] == []
    schedules = {row["item_id"]: row for row in restored["review_schedules"]}
    for original in (legacy, retired):
        row = schedules[original["item_id"]]
        assert row == {
            **{
                key: value
                for key, value in original.items()
                if key not in {"interval_days", "ease_factor"}
            },
            "fsrs_state": 1,
            "fsrs_step": 0,
            "fsrs_stability": None,
            "fsrs_difficulty": None,
            "legacy_interval_days": 7.25,
            "legacy_ease_factor": 2.65,
        }
    due = db_session.get(ReviewSchedule, (OWNER, "legacy-due"))
    assert due is not None and due.is_due(datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC))
    _delete(client, headers)
    assert client.post("/v1/imports", json=restored, headers=headers).status_code == 200
    again = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers).json()
    assert again["review_schedules"] == restored["review_schedules"]
    assert again["schedule_review_observations"] == []


@pytest.mark.parametrize(
    "missing", ["fsrs_state", "fsrs_step", "fsrs_stability", "fsrs_difficulty"]
)
def test_partial_modern_schedule_is_not_treated_as_legacy(
    client: TestClient, db_session: Session, missing: str
) -> None:
    archive, headers = _archive(client, db_session)
    del archive["review_schedules"][0][missing]
    _assert_rejected(client, db_session, archive, headers)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fsrs_state", True),
        ("fsrs_state", "2"),
        ("fsrs_step", 0),
        ("fsrs_stability", None),
        ("fsrs_difficulty", 11),
        ("fsrs_difficulty", True),
        ("legacy_interval_days", "3.75"),
        ("interval_days", 2),
        ("player_id", "foreign-player"),
    ],
)
def test_malformed_schedule_rejects_entire_import(
    client: TestClient, db_session: Session, field: str, value: Any
) -> None:
    archive, headers = _archive(client, db_session)
    archive["review_schedules"][0][field] = value
    _assert_rejected(client, db_session, archive, headers)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("player_id", "foreign-player"),
        ("item_id", "missing-parent"),
        ("source_id", "missing-run"),
        ("source_kind", "lesson"),
        ("success", False),
        ("success", 1),
        ("hints_used", None),
        ("hints_used", True),
        ("elapsed_ms", 1),
        ("rating", 3),
        ("reviewed_at", "2026-01-01T00:00:00"),
        ("input_provenance", "app_reported"),
        ("verification_status", "unknown"),
    ],
)
def test_invalid_observation_rejects_entire_import(
    client: TestClient, db_session: Session, field: str, value: Any
) -> None:
    archive, headers = _archive(client, db_session)
    archive["schedule_review_observations"][0][field] = value
    _assert_rejected(client, db_session, archive, headers)


def test_partial_observation_rejects_entire_import(client: TestClient, db_session: Session) -> None:
    archive, headers = _archive(client, db_session)
    del archive["schedule_review_observations"][0]["hints_used"]
    _assert_rejected(client, db_session, archive, headers)


def test_observation_requires_its_archived_parent(client: TestClient, db_session: Session) -> None:
    archive, headers = _archive(client, db_session)
    archive["review_schedules"] = []
    _assert_rejected(client, db_session, archive, headers)


@pytest.mark.parametrize("source_kind", ["lesson", "transfer"])
def test_observation_cannot_relabel_a_puzzle_source(
    client: TestClient, db_session: Session, source_kind: str
) -> None:
    archive, headers = _archive(client, db_session)
    observation = archive["schedule_review_observations"][0]
    observation["source_kind"] = source_kind
    observation["input_provenance"] = "app_reported"
    _assert_rejected(client, db_session, archive, headers)


def test_foreign_existing_source_cannot_be_injected(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _archive(client, db_session)
    _create_player_token(client, "fsrs-foreign-source")
    foreign_run = seed_puzzle_run(db_session, "fsrs-foreign-source", solved=True)
    archive["puzzle_runs"][0]["id"] = foreign_run.id
    archive["schedule_review_observations"][0]["source_id"] = foreign_run.id
    archive["content_attempts"][0]["id"] = foreign_run.id
    archive["content_attempts"][0]["response_payload"]["run_id"] = foreign_run.id
    _assert_rejected(client, db_session, archive, headers)
    db_session.refresh(foreign_run)
    assert foreign_run.player_id == "fsrs-foreign-source"


@pytest.mark.parametrize("duplicate", ["schedule", "observation_id", "source"])
def test_duplicate_review_identity_rejects_entire_import(
    client: TestClient, db_session: Session, duplicate: str
) -> None:
    archive, headers = _archive(client, db_session)
    if duplicate == "schedule":
        archive["review_schedules"].append(deepcopy(archive["review_schedules"][0]))
    else:
        observation = deepcopy(archive["schedule_review_observations"][0])
        if duplicate == "source":
            observation["id"] = str(uuid4())
        archive["schedule_review_observations"].append(observation)
    _assert_rejected(client, db_session, archive, headers)


def _lesson_and_transfer_archive(
    client: TestClient, session: Session
) -> tuple[dict[str, Any], dict[str, str]]:
    headers = _authorization(_create_player_token(client, OWNER))
    _create_player_token(client, "fsrs-archive-coach")
    records = _seed_player_lifecycle_records(session, OWNER, "fsrs-archive-coach")
    opportunity_id = records["opportunity_id"]
    assert isinstance(opportunity_id, UUID)
    lesson = session.exec(select(LessonAttempt).where(LessonAttempt.player_id == OWNER)).one()
    lesson.lesson_id = str(opportunity_id)
    lesson.source_kind = "persisted_opportunity"
    lesson.grading_status = "verified"
    lesson_schedule = ReviewSchedule(
        player_id=OWNER, item_id=lesson.lesson_id, next_review_at=lesson.completed_at
    )
    lesson_observation = lesson_schedule.update(
        success=True,
        current_time=lesson.completed_at,
        hints_used=lesson.hints_used,
        elapsed_ms=lesson.elapsed_ms,
        source_kind="lesson",
        source_id=lesson.id,
    )
    transfer = session.exec(
        select(TransferMeasurement).where(TransferMeasurement.player_id == OWNER)
    ).one()
    transfer.scheduled_for = lesson.completed_at
    transfer.completed_at = lesson.completed_at
    transfer.succeeded = False
    transfer_schedule = ReviewSchedule(
        player_id=OWNER,
        item_id=transfer.id,
        skill_id=transfer.skill_id,
        next_review_at=transfer.scheduled_for,
    )
    transfer_observation = transfer_schedule.update(
        success=False,
        current_time=transfer.completed_at,
        hints_used=None,
        elapsed_ms=None,
        source_kind="transfer",
        source_id=transfer.id,
    )
    transfer_schedule.retired_at = transfer.completed_at
    transfer_schedule.retirement_reason = "transfer_measurement_completed"
    session.add_all([lesson, transfer, lesson_schedule, transfer_schedule])
    session.flush()
    session.add_all([lesson_observation, transfer_observation])
    session.commit()
    response = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers)
    assert response.status_code == 200, response.text
    archive = response.json()
    _delete(client, headers)
    session.expire_all()
    return archive, headers


def test_lesson_and_transfer_sources_roundtrip_as_historical(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _lesson_and_transfer_archive(client, db_session)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers).json()
    assert restored["review_schedules"] == archive["review_schedules"]
    assert sorted(restored["schedule_review_observations"], key=lambda row: row["id"]) == sorted(
        [
            {**row, "verification_status": "historical"}
            for row in archive["schedule_review_observations"]
        ],
        key=lambda row: row["id"],
    )
    transfer = next(
        row for row in restored["schedule_review_observations"] if row["source_kind"] == "transfer"
    )
    assert transfer["hints_used"] is None and transfer["elapsed_ms"] is None


def test_completed_legacy_transfer_without_schedule_or_observation_is_valid(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _lesson_and_transfer_archive(client, db_session)
    completed_id = archive["transfer_measurements"][0]["id"]
    archive["schedule_review_observations"] = [
        row for row in archive["schedule_review_observations"] if row["source_kind"] != "transfer"
    ]
    archive["review_schedules"] = [
        row for row in archive["review_schedules"] if row["item_id"] != completed_id
    ]
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    assert db_session.get(ReviewSchedule, (OWNER, completed_id)) is None
    restored = db_session.get(TransferMeasurement, completed_id)
    assert restored is not None and restored.completed_at is not None


@pytest.mark.parametrize(
    ("collection", "field", "value"),
    [
        ("lesson_attempts", "lesson_id", "wrong-item"),
        ("lesson_attempts", "player_id", "foreign-player"),
        ("lesson_attempts", "grading_status", "ungraded"),
        ("lesson_attempts", "hints_used", 1),
        ("lesson_attempts", "elapsed_ms", 99),
        ("lesson_attempts", "success", False),
        ("transfer_measurements", "player_id", "foreign-player"),
        ("transfer_measurements", "succeeded", True),
        ("transfer_measurements", "completed_at", None),
    ],
)
def test_lesson_and_transfer_source_mismatch_rejects_import(
    client: TestClient,
    db_session: Session,
    collection: str,
    field: str,
    value: Any,
) -> None:
    archive, headers = _lesson_and_transfer_archive(client, db_session)
    archive[collection][0][field] = value
    _assert_rejected(client, db_session, archive, headers)


def _sparring_archive(
    client: TestClient,
    session: Session,
    *,
    source_ply: int = 1,
    with_observation: bool = True,
) -> tuple[dict[str, Any], dict[str, str]]:
    session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    headers = _authorization(_create_player_token(client, OWNER))
    seed_graph(session, OWNER, source_ply=source_ply, with_observation=with_observation)
    response = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers)
    assert response.status_code == 200, response.text
    archive = response.json()
    _delete(client, headers)
    session.expire_all()
    return archive, headers


@pytest.mark.parametrize("source_ply", [0, 1])
def test_sparring_card_and_review_roundtrip_without_replaying_terminal_attempt(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, source_ply: int
) -> None:
    archive, headers = _sparring_archive(client, db_session, source_ply=source_ply)
    attempt = archive["sparring_attempts"][0]
    observation = archive["schedule_review_observations"][0]
    schedule = archive["review_schedules"][0]
    assert observation["source_kind"] == "sparring"
    assert observation["source_id"] == attempt["id"]
    assert observation["item_id"] == source_item_id(
        UUID(attempt["source_review_id"]), UUID(attempt["source_position_id"])
    )
    assert observation["success"] is True and observation["rating"] == 3
    assert observation["reviewed_at"] == attempt["completed_at"]
    assert observation["hints_used"] is None and observation["elapsed_ms"] is None
    assert observation["input_provenance"] == "server_recorded"
    assert schedule["fsrs_stability"] > 0 and schedule["fsrs_difficulty"] >= 1
    assert datetime.fromisoformat(schedule["next_review_at"]) > datetime.fromisoformat(
        attempt["completed_at"]
    )

    def no_replay(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Archived sparring reviews must not run the scheduler")

    monkeypatch.setattr(ReviewSchedule, "update", no_replay)
    for _ in range(2):
        response = client.post("/v1/imports", json=archive, headers=headers)
        assert response.status_code == 200, response.text
        resumed = client.get(f"/v1/play-sessions/{attempt['play_session_id']}", headers=headers)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["sparring"]["verification_status"] == "historical"
        assert resumed.json()["sparring"]["outcome"] == "win"
        repeated = client.post(
            f"/v1/play-sessions/{attempt['play_session_id']}/resign", headers=headers
        )
        assert repeated.status_code == 409, repeated.text
        restored = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers).json()
        assert restored["review_schedules"] == archive["review_schedules"]
        assert restored["schedule_review_observations"] == [
            {**observation, "verification_status": "historical"}
        ]
        assert restored["sparring_attempts"] == [{**attempt, "verification_status": "historical"}]
        assert restored["habit_credits"] == archive["habit_credits"] == []
        assert restored["pattern_awards"] == archive["pattern_awards"] == []
        assert restored["positive_move_credits"] == archive["positive_move_credits"] == []
        _delete(client, headers)
        archive = restored


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("player_id", "foreign-player"),
        ("source_kind", "transfer"),
        ("source_id", str(UUID(int=0))),
        ("success", False),
        ("hints_used", 0),
        ("elapsed_ms", 0),
        ("elapsed_ms", 600_000),
        ("rating", 4),
        ("reviewed_at", "2026-01-01T00:11:00"),
        ("input_provenance", "app_reported"),
    ],
)
def test_forged_sparring_review_is_rejected_atomically(
    client: TestClient, db_session: Session, field: str, value: Any
) -> None:
    archive, headers = _sparring_archive(client, db_session)
    observation = archive["schedule_review_observations"][0]
    observation[field] = value
    if field == "success":
        # A self-consistent grade still cannot contradict the authoritative outcome.
        observation["rating"] = 1
    _assert_rejected(client, db_session, archive, headers)
    assert (
        db_session.exec(select(SparringAttempt).where(SparringAttempt.player_id == OWNER)).all()
        == []
    )


def test_sparring_review_cannot_bind_a_different_owned_source_item(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _sparring_archive(client, db_session)
    attempt = archive["sparring_attempts"][0]
    other_position = next(
        row for row in archive["positions"] if row["id"] != attempt["source_position_id"]
    )
    wrong_item = source_item_id(UUID(attempt["source_review_id"]), UUID(other_position["id"]))
    archive["review_schedules"][0]["item_id"] = wrong_item
    archive["schedule_review_observations"][0]["item_id"] = wrong_item
    _assert_rejected(client, db_session, archive, headers)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("completed_at", None),
        ("completed_at", "2026-01-01T00:11:00"),
        ("outcome", "loss"),
        ("result", "1-0"),
        ("source_review_id", str(UUID(int=0))),
        ("source_position_id", str(UUID(int=0))),
        ("source_analysis_id", str(UUID(int=0))),
    ],
)
def test_sparring_review_requires_its_terminal_provenance_graph(
    client: TestClient, db_session: Session, field: str, value: Any
) -> None:
    archive, headers = _sparring_archive(client, db_session)
    archive["sparring_attempts"][0][field] = value
    _assert_rejected(client, db_session, archive, headers)


def test_sparring_review_requires_its_archived_attempt(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _sparring_archive(client, db_session)
    del archive["sparring_attempts"]
    _assert_rejected(client, db_session, archive, headers)


def test_existing_foreign_sparring_review_source_is_never_overwritten(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _sparring_archive(client, db_session)
    foreign_id = "fsrs-foreign-sparring"
    foreign_headers = _authorization(_create_player_token(client, foreign_id))
    foreign = seed_graph(db_session, foreign_id, with_observation=True)
    before = client.post("/v1/exports", json={"player_id": foreign_id}, headers=foreign_headers)
    assert before.status_code == 200, before.text
    archive["sparring_attempts"][0]["id"] = str(foreign.id)
    archive["schedule_review_observations"][0]["source_id"] = str(foreign.id)
    _assert_rejected(client, db_session, archive, headers)
    after = client.post("/v1/exports", json={"player_id": foreign_id}, headers=foreign_headers)
    assert after.status_code == 200, after.text
    assert after.json() == before.json()


def test_pre_fsrs_sparring_archive_preserves_due_state_without_inventing_reviews(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, headers = _sparring_archive(client, db_session, with_observation=False)
    del archive["schedule_review_observations"]
    schedule = archive["review_schedules"][0]
    for field in ("fsrs_state", "fsrs_step", "fsrs_stability", "fsrs_difficulty"):
        del schedule[field]
    schedule["interval_days"] = schedule.pop("legacy_interval_days")
    schedule["ease_factor"] = schedule.pop("legacy_ease_factor")
    expected = {
        key: value for key, value in schedule.items() if key not in {"interval_days", "ease_factor"}
    }
    expected.update(
        fsrs_state=1,
        fsrs_step=0,
        fsrs_stability=None,
        fsrs_difficulty=None,
        legacy_interval_days=17.0,
        legacy_ease_factor=2.7,
    )

    def no_replay(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Legacy terminal attempts carry no recoverable FSRS grade")

    monkeypatch.setattr(ReviewSchedule, "update", no_replay)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    play_id = archive["sparring_attempts"][0]["play_session_id"]
    repeated = client.post(f"/v1/play-sessions/{play_id}/resign", headers=headers)
    assert repeated.status_code == 409, repeated.text
    restored = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers).json()
    assert restored["review_schedules"] == [expected]
    assert restored["schedule_review_observations"] == []
    assert restored["sparring_attempts"] == [
        {**archive["sparring_attempts"][0], "verification_status": "historical"}
    ]
