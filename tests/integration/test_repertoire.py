from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from scan64.api.app import app
from scan64.api.models import Player, PlayerProfile
from scan64.chess.games.models import Game
from scan64.learning.habit_models import HabitCredit
from scan64.learning.repertoire import Repertoire, RepertoireAttempt
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule, ScheduleReviewObservation
from scan64.persistence.database import get_session
from tests.integration.test_data_lifecycle_completeness import _authorization, _create_player_token
from tests.unit.test_repertoire import PGN

OWNER = "repertoire-owner"


def seed(client: TestClient) -> tuple[dict[str, str], dict[str, Any]]:
    headers = _authorization(_create_player_token(client, OWNER))
    response = client.post("/v1/repertoires", json={"pgn": PGN}, headers=headers)
    assert response.status_code == 201, response.text
    return headers, response.json()[0]


def review(
    client: TestClient, headers: dict[str, str], repertoire: dict[str, Any], **changes: Any
) -> Any:
    body = {"attempt_id": str(uuid4()), "node_id": 0, "submitted_move": "e2e4", **changes}
    return client.post(f"/v1/repertoires/{repertoire['id']}/attempts", json=body, headers=headers)


def delete_owner(client: TestClient, headers: dict[str, str]) -> None:
    response = client.request(
        "DELETE",
        f"/v1/players/{OWNER}/data",
        headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{OWNER}"},
    )
    assert response.status_code == 200, response.text


def test_import_playback_coverage_verified_retry_and_both_sides(
    client: TestClient, db_session: Session
) -> None:
    headers, repertoire = seed(client)
    assert repertoire["total_decisions"] == 5
    assert repertoire["reviewed_decisions"] == 0
    assert repertoire["due_decisions"] == 5
    profile = db_session.get(PlayerProfile, OWNER)
    before_rating = profile.rating if profile else None
    attempt_id = str(uuid4())
    first = review(client, headers, repertoire, attempt_id=attempt_id)
    assert first.status_code == 200, first.text
    assert first.json()["success"] and first.json()["rating"] == 3
    assert first.json()["repertoire"]["reviewed_decisions"] == 1
    assert first.json()["repertoire"]["due_decisions"] == 4
    duplicate = review(client, headers, repertoire, attempt_id=attempt_id)
    assert duplicate.status_code == 200
    assert duplicate.json()["next_review_at"] == first.json()["next_review_at"]
    assert len(db_session.exec(select(RepertoireAttempt)).all()) == 1
    assert len(db_session.exec(select(ReviewSchedule)).all()) == 1
    conflict = review(client, headers, repertoire, attempt_id=attempt_id, submitted_move="d2d4")
    assert conflict.status_code == 409
    # Node 1 is Black's decision after e4, and c5 is a retained alternative.
    black = review(client, headers, repertoire, node_id=1, submitted_move="c7c5")
    assert black.status_code == 200 and black.json()["success"]
    wrong = review(client, headers, repertoire, node_id=1, submitted_move="g8f6")
    assert wrong.status_code == 200 and not wrong.json()["success"]
    assert wrong.json()["rating"] == 1
    reloaded = client.get(f"/v1/repertoires/{repertoire['id']}", headers=headers).json()
    assert reloaded["tree"] == repertoire["tree"]
    assert reloaded["reviewed_decisions"] == 2
    assert len(db_session.exec(select(ReviewSchedule)).all()) == 2
    observations = db_session.exec(select(ScheduleReviewObservation)).all()
    assert len(observations) == 3
    assert all(
        row.source_kind == "repertoire" and row.input_provenance == "server_recorded"
        for row in observations
    )
    assert db_session.exec(select(Game)).all() == []
    assert db_session.exec(select(HabitCredit)).all() == []
    db_session.expire_all()
    profile = db_session.get(PlayerProfile, OWNER)
    assert (profile.rating if profile else None) == before_rating


@pytest.mark.parametrize(
    "changes",
    [
        {"submitted_move": "e2e8"},
        {"submitted_move": "0000"},
        {"submitted_move": "e7e5"},
        {"node_id": 999},
        {"node_id": 3},
        {"node_id": True},
        {"attempt_id": "not-a-uuid"},
        {"success": True},
    ],
)
def test_bad_drill_input_never_creates_learning_state(
    client: TestClient, db_session: Session, changes: dict[str, Any]
) -> None:
    headers, repertoire = seed(client)
    response = review(client, headers, repertoire, **changes)
    assert response.status_code == 422, response.text
    assert db_session.exec(select(RepertoireAttempt)).all() == []
    assert db_session.exec(select(ReviewSchedule)).all() == []
    assert db_session.exec(select(ScheduleReviewObservation)).all() == []


def test_invalid_batch_and_other_owner_requests_are_atomic(
    client: TestClient, db_session: Session
) -> None:
    headers, repertoire = seed(client)
    other = _authorization(_create_player_token(client, "repertoire-other"))
    bad = client.post(
        "/v1/repertoires", headers=headers, json={"pgn": PGN + "\n1. e4 e5 2. e8 *\n"}
    )
    assert bad.status_code == 422
    assert len(db_session.exec(select(Repertoire)).all()) == 1
    assert client.get("/v1/repertoires", headers=other).json() == []
    assert client.get(f"/v1/repertoires/{repertoire['id']}", headers=other).status_code == 404
    assert review(client, other, repertoire).status_code == 404
    assert client.post("/v1/repertoires", json={"pgn": PGN}).status_code == 401
    assert db_session.exec(select(RepertoireAttempt)).all() == []
    assert db_session.exec(select(ReviewSchedule)).all() == []


def test_concurrent_retry_and_distinct_attempts_serialize_fsrs(
    client: TestClient, db_session: Session
) -> None:
    headers, repertoire = seed(client)
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)
    previous = app.dependency_overrides[get_session]

    def independent_session() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = independent_session
    try:
        attempt_id = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            retries = list(
                pool.map(
                    lambda _: review(client, headers, repertoire, attempt_id=attempt_id), range(2)
                )
            )
        assert [result.status_code for result in retries] == [200, 200]
        assert retries[0].json()["next_review_at"] == retries[1].json()["next_review_at"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda move: review(client, headers, repertoire, submitted_move=move),
                    ["e2e4", "a2a3"],
                )
            )
        assert [result.status_code for result in results] == [200, 200]
        db_session.expire_all()
        attempts = db_session.exec(
            select(RepertoireAttempt).order_by(RepertoireAttempt.sequence)
        ).all()
        assert [attempt.sequence for attempt in attempts] == [1, 2, 3]
        assert len(db_session.exec(select(ReviewSchedule)).all()) == 1
        # Archive import replays all three reviews and rejects any lost update.
        archive = client.post("/v1/exports", headers=headers, json={"player_id": OWNER}).json()
        delete_owner(client, headers)
        restored = client.post("/v1/imports", headers=headers, json=archive)
        assert restored.status_code == 200, restored.text
    finally:
        app.dependency_overrides[get_session] = previous


def test_archive_roundtrip_and_reset_preserve_content_not_new_evidence(
    client: TestClient, db_session: Session
) -> None:
    db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    headers, repertoire = seed(client)
    assert review(client, headers, repertoire).status_code == 200
    archive = client.post("/v1/exports", headers=headers, json={"player_id": OWNER}).json()
    assert archive["repertoires"][0]["id"] == repertoire["id"]
    dry = client.request(
        "DELETE", f"/v1/players/{OWNER}/data", headers=headers, json={"dry_run": True}
    )
    assert dry.json()["affected_rows"]["repertoires"] == 1
    assert dry.json()["affected_rows"]["repertoire_attempts"] == 1
    other = _authorization(_create_player_token(client, "other-repertoire-owner"))
    other_import = client.post("/v1/repertoires", headers=other, json={"pgn": PGN})
    assert other_import.status_code == 201
    delete_owner(client, headers)
    db_session.expire_all()
    assert db_session.get(Repertoire, UUID(repertoire["id"])) is None
    assert db_session.exec(select(RepertoireAttempt)).all() == []
    assert len(client.get("/v1/repertoires", headers=other).json()) == 1
    restored = client.post("/v1/imports", headers=headers, json=archive)
    assert restored.status_code == 200, restored.text
    again = client.post("/v1/exports", headers=headers, json={"player_id": OWNER}).json()
    assert again["repertoires"] == archive["repertoires"]
    assert again["review_schedules"] == archive["review_schedules"]
    assert again["repertoire_attempts"] == [
        {**archive["repertoire_attempts"][0], "verification_status": "historical"}
    ]
    assert again["schedule_review_observations"][0]["verification_status"] == "historical"
    reset = client.post(
        f"/v1/players/{OWNER}/progress/reset",
        headers=headers,
        json={"confirmation": "RESET PROGRESS"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["cleared"]["repertoire_attempts"] == 1
    content = client.get(f"/v1/repertoires/{repertoire['id']}", headers=headers).json()
    assert content["tree"] == repertoire["tree"]
    assert (
        content["reviewed_decisions"] == 0
        and content["due_decisions"] == content["total_decisions"]
    )
    assert review(client, headers, repertoire).status_code == 200


@pytest.mark.parametrize(
    "mutation",
    [
        "tree",
        "owner",
        "attempt_owner",
        "move",
        "source",
        "missing_repertoire",
        "missing_attempt",
        "missing_observation",
        "schedule",
        "sequence",
        "retained_identity",
    ],
)
def test_archive_rejects_fabricated_or_foreign_repertoire_graph(
    client: TestClient, db_session: Session, mutation: str
) -> None:
    headers, repertoire = seed(client)
    assert review(client, headers, repertoire).status_code == 200
    archive = client.post("/v1/exports", headers=headers, json={"player_id": OWNER}).json()
    delete_owner(client, headers)
    tampered = deepcopy(archive)
    if mutation == "tree":
        tampered["repertoires"][0]["tree"]["nodes"][0]["fen"] = "not a FEN"
    elif mutation == "owner":
        tampered["repertoires"][0]["player_id"] = "other"
    elif mutation == "attempt_owner":
        tampered["repertoire_attempts"][0]["player_id"] = "other"
    elif mutation == "move":
        tampered["repertoire_attempts"][0]["submitted_move"] = "a2a3"
    elif mutation == "source":
        tampered["schedule_review_observations"][0]["source_kind"] = "sparring"
    elif mutation == "missing_repertoire":
        tampered["repertoires"] = []
    elif mutation == "missing_attempt":
        tampered["repertoire_attempts"] = []
    elif mutation == "missing_observation":
        tampered["schedule_review_observations"] = []
    elif mutation == "schedule":
        tampered["review_schedules"][0]["fsrs_difficulty"] = 9.0
    elif mutation == "sequence":
        tampered["repertoire_attempts"][0]["sequence"] = 2
    else:
        # A retained object belonging to another player must never be a fallback.
        row = Repertoire.model_validate({**archive["repertoires"][0], "player_id": "other"})
        db_session.add(row)
        db_session.commit()
    response = client.post("/v1/imports", headers=headers, json=tampered)
    assert response.status_code == 400, response.text
    db_session.expire_all()
    assert db_session.get(Player, OWNER) is None
    assert db_session.exec(select(RepertoireAttempt)).all() == []
    assert db_session.exec(select(ReviewSchedule)).all() == []
