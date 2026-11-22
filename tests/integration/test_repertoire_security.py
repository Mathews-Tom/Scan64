from __future__ import annotations

import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

import scan64.api.data_lifecycle as lifecycle
from scan64.api.app import app
from scan64.api.middleware import IdempotencyRecord
from scan64.api.repertoire_body import MAX_ATTEMPT_BODY_BYTES, MAX_IMPORT_BODY_BYTES
from scan64.learning.repertoire import MAX_NODES, MAX_PGN_BYTES, Repertoire, RepertoireAttempt
from scan64.persistence.database import get_session
from tests.integration.test_repertoire import OWNER, delete_owner, review, seed


def exported(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    result = client.post("/v1/exports", headers=headers, json={"player_id": OWNER})
    assert result.status_code == 200, result.text
    return result.json()


def test_same_cache_key_cannot_replay_cleared_export_or_turn_confirmed_delete_into_dry_run(
    client: TestClient,
    db_session: Session,
) -> None:
    headers, repertoire = seed(client)
    headers = {**headers, "Idempotency-Key": "same-owner-lifecycle-key"}
    assert review(client, headers, repertoire).status_code == 200
    assert len(exported(client, headers)["repertoire_attempts"]) == 1
    reset = client.post(
        f"/v1/players/{OWNER}/progress/reset",
        headers=headers,
        json={"confirmation": "RESET PROGRESS"},
    )
    assert reset.status_code == 200
    assert exported(client, headers)["repertoire_attempts"] == []
    assert review(client, headers, repertoire).status_code == 200
    # A second reset with the same key must execute, not replay the first reset.
    assert (
        client.post(
            f"/v1/players/{OWNER}/progress/reset",
            headers=headers,
            json={"confirmation": "RESET PROGRESS"},
        ).status_code
        == 200
    )
    assert exported(client, headers)["repertoire_attempts"] == []
    assert client.request(
        "DELETE", f"/v1/players/{OWNER}/data", headers=headers, json={"dry_run": True}
    ).json()["dry_run"]
    delete_owner(client, headers)
    revoked_export = client.post("/v1/exports", headers=headers, json={"player_id": OWNER})
    assert revoked_export.status_code == 403
    assert "repertoires" not in revoked_export.json()
    assert db_session.exec(select(IdempotencyRecord)).all() == []


def test_conflicting_attempt_payload_reaches_persisted_verifier_under_same_generic_key(
    client: TestClient,
    db_session: Session,
) -> None:
    headers, repertoire = seed(client)
    headers = {**headers, "Idempotency-Key": "same-drill-key"}
    attempt_id = str(uuid4())
    first = review(client, headers, repertoire, attempt_id=attempt_id)
    assert first.status_code == 200
    retry = review(client, headers, repertoire, attempt_id=attempt_id)
    assert retry.status_code == 200
    assert retry.json()["next_review_at"] == first.json()["next_review_at"]
    conflict = review(client, headers, repertoire, attempt_id=attempt_id, submitted_move="a2a3")
    assert conflict.status_code == 409
    assert len(db_session.exec(select(RepertoireAttempt)).all()) == 1
    assert db_session.exec(select(IdempotencyRecord)).all() == []


@pytest.mark.parametrize(
    "path,method,body",
    [
        ("/v1/exports", "POST", {"player_id": OWNER}),
        ("/v1/imports", "POST", None),
        (
            f"/v1/players/{OWNER}/data",
            "DELETE",
            {"dry_run": False, "confirmation": f"delete-{OWNER}"},
        ),
        (f"/v1/players/{OWNER}/progress/reset", "POST", {"confirmation": "RESET PROGRESS"}),
    ],
)
def test_old_generic_cache_record_cannot_bypass_wrong_token_lifecycle_authorization(
    client: TestClient,
    db_session: Session,
    path: str,
    method: str,
    body: dict[str, Any] | None,
) -> None:
    owner_headers, repertoire = seed(client)
    assert review(client, owner_headers, repertoire).status_code == 200
    if body is None:
        body = exported(client, owner_headers)
        delete_owner(client, owner_headers)
    authorization = "Bearer invalid"
    principal = hashlib.sha256(authorization.encode()).hexdigest()
    db_session.add(
        IdempotencyRecord(
            idempotency_key=f"{method}:{path}:{principal}:old-key",
            status_code=200,
            response_body='{"leaked":true}',
            headers="{}",
        )
    )
    db_session.commit()
    response = client.request(
        method,
        path,
        headers={
            "Authorization": authorization,
            "Idempotency-Key": "old-key",
        },
        json=body,
    )
    expected_status = 401 if path.endswith("/progress/reset") else 403
    assert response.status_code == expected_status, response.text
    assert response.json() != {"leaked": True}
    assert len(db_session.exec(select(RepertoireAttempt)).all()) == (
        0 if path == "/v1/imports" else 1
    )


def test_export_finishing_after_deletion_is_not_published_for_revoked_bearer(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, repertoire = seed(client)
    assert review(client, headers, repertoire).status_code == 200
    headers = {**headers, "Idempotency-Key": "in-flight-export"}
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)
    original_override = app.dependency_overrides[get_session]

    def independent_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    ready, release = Event(), Event()
    archive_model = lifecycle.ExportArchive

    def paused_archive(**values: Any) -> Any:
        ready.set()
        assert release.wait(10)
        return archive_model(**values)

    app.dependency_overrides[get_session] = independent_session
    monkeypatch.setattr(lifecycle, "ExportArchive", paused_archive)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                client.post, "/v1/exports", headers=headers, json={"player_id": OWNER}
            )
            try:
                assert ready.wait(10)
                delete_owner(client, headers)
            finally:
                release.set()
            # An already-authorized read may finish, but never create a replay.
            assert pending.result(timeout=10).status_code == 200
        assert (
            client.post("/v1/exports", headers=headers, json={"player_id": OWNER}).status_code
            == 403
        )
        with Session(engine) as session:
            assert session.exec(select(IdempotencyRecord)).all() == []
    finally:
        release.set()
        app.dependency_overrides[get_session] = original_override


@pytest.mark.parametrize("kind", ["import", "attempt"])
def test_new_routes_reject_oversized_body_before_json_parse_without_writes(
    client: TestClient,
    db_session: Session,
    kind: str,
) -> None:
    headers, repertoire = seed(client)
    path, limit = (
        ("/v1/repertoires", MAX_IMPORT_BODY_BYTES)
        if kind == "import"
        else (
            f"/v1/repertoires/{repertoire['id']}/attempts",
            MAX_ATTEMPT_BODY_BYTES,
        )
    )
    # Not JSON: reaching FastAPI's decoder would yield 422, not the streaming 413.
    response = client.post(
        path, headers={**headers, "Content-Type": "application/json"}, content=b"x" * (limit + 1)
    )
    assert response.status_code == 413
    assert len(db_session.exec(select(Repertoire)).all()) == 1
    assert db_session.exec(select(RepertoireAttempt)).all() == []


def test_valid_worst_case_json_escaping_is_supported(client: TestClient) -> None:
    headers, _ = seed(client)
    raw = "1. e4 {" + "a" * (MAX_PGN_BYTES - 300) + "} *"
    encoded = '{"pgn":"' + "".join(f"\\u{ord(character):04x}" for character in raw) + '"}'
    assert len(encoded) < MAX_IMPORT_BODY_BYTES
    response = client.post(
        "/v1/repertoires",
        headers={**headers, "Content-Type": "application/json"},
        content=encoded.encode(),
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("counter", ["9" * 3000, "1000001", "-1"])
def test_fen_counter_amplification_is_rejected_atomically(
    client: TestClient,
    db_session: Session,
    counter: str,
) -> None:
    headers, _ = seed(client)
    pgn = f'[FEN "7k/8/8/8/8/8/4p3/K7 b - - 0 {counter}"]\n[SetUp "1"]\n\n1... e1=N *'
    response = client.post("/v1/repertoires", headers=headers, json={"pgn": pgn})
    assert response.status_code == 422, response.text
    assert len(db_session.exec(select(Repertoire)).all()) == 1


def test_empty_event_import_roundtrips_and_normalized_oversize_is_atomic(
    client: TestClient,
    db_session: Session,
) -> None:
    headers, _ = seed(client)
    imported = client.post(
        "/v1/repertoires", headers=headers, json={"pgn": '[Event ""]\n\n1. e4 *'}
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()[0]["title"] == "Repertoire"
    # Normalization adds the default headers and must not silently exceed restore's cap.
    raw = "{" + "a" * (MAX_PGN_BYTES - len("{} 1. e4 *")) + "} 1. e4 *"
    assert len(raw.encode()) == MAX_PGN_BYTES
    rejected = client.post("/v1/repertoires", headers=headers, json={"pgn": raw})
    assert rejected.status_code == 422 and "Normalized" in rejected.json()["detail"]
    assert len(db_session.exec(select(Repertoire)).all()) == 2
    archive = exported(client, headers)
    delete_owner(client, headers)
    restored = client.post("/v1/imports", headers=headers, json=archive)
    assert restored.status_code == 200, restored.text
    assert exported(client, headers)["repertoires"] == archive["repertoires"]


@pytest.mark.parametrize("mutation", ["nodes", "children", "fen", "pgn", "title", "move"])
def test_new_archive_fields_are_bounded_without_a_total_legacy_archive_limit(
    client: TestClient,
    db_session: Session,
    mutation: str,
) -> None:
    headers, repertoire = seed(client)
    assert review(client, headers, repertoire).status_code == 200
    archive = exported(client, headers)
    delete_owner(client, headers)
    row = archive["repertoires"][0]
    if mutation == "nodes":
        row["tree"]["nodes"] *= MAX_NODES
    elif mutation == "children":
        row["tree"]["nodes"][0]["children"] *= MAX_NODES
    elif mutation == "fen":
        row["tree"]["nodes"][0]["fen"] += "0" * 1000
    elif mutation == "pgn":
        row["pgn"] = "x" * (MAX_PGN_BYTES + 1)
    elif mutation == "title":
        row["title"] = "x" * 201
    else:
        archive["repertoire_attempts"][0]["submitted_move"] = "x" * 1000
    response = client.post("/v1/imports", headers=headers, json=archive)
    assert response.status_code == 400
    assert db_session.exec(select(Repertoire)).all() == []
    assert db_session.exec(select(RepertoireAttempt)).all() == []


@pytest.mark.parametrize(
    "field,group",
    [
        ("created_at", "repertoires"),
        ("completed_at", "repertoire_attempts"),
        ("reviewed_at", "schedule_review_observations"),
        ("next_review_at", "review_schedules"),
        ("last_reviewed_at", "review_schedules"),
    ],
)
@pytest.mark.parametrize(
    "value", ["not-a-date", "0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59", 0]
)
def test_malformed_and_extreme_new_archive_timestamps_reject_without_writes(
    client: TestClient,
    db_session: Session,
    field: str,
    group: str,
    value: Any,
) -> None:
    headers, repertoire = seed(client)
    assert review(client, headers, repertoire).status_code == 200
    archive = exported(client, headers)
    delete_owner(client, headers)
    archive[group][0][field] = value
    response = client.post("/v1/imports", headers=headers, json=archive)
    assert response.status_code == 400, response.text
    assert db_session.exec(select(Repertoire)).all() == []
    assert db_session.exec(select(RepertoireAttempt)).all() == []


def shifted_archive(archive: dict[str, Any], offset: timedelta) -> dict[str, Any]:
    result = deepcopy(archive)
    for group, fields in (
        ("repertoires", ["created_at"]),
        ("repertoire_attempts", ["completed_at"]),
        ("schedule_review_observations", ["reviewed_at"]),
        ("review_schedules", ["next_review_at", "last_reviewed_at"]),
    ):
        for row in result[group]:
            for field in fields:
                row[field] = (datetime.fromisoformat(row[field]) + offset).isoformat()
    return result


def test_consistent_future_archive_is_rejected_but_allowed_skew_keeps_live_replay_monotonic(
    client: TestClient,
    db_session: Session,
) -> None:
    headers, repertoire = seed(client)
    assert review(client, headers, repertoire).status_code == 200
    archive = exported(client, headers)
    delete_owner(client, headers)
    rejected = client.post(
        "/v1/imports", headers=headers, json=shifted_archive(archive, timedelta(days=1))
    )
    assert rejected.status_code == 400
    assert db_session.exec(select(Repertoire)).all() == []
    accepted = client.post(
        "/v1/imports", headers=headers, json=shifted_archive(archive, timedelta(minutes=2))
    )
    assert accepted.status_code == 200, accepted.text
    # This machine's current clock is behind the accepted historical review.
    assert review(client, headers, repertoire).status_code == 200
    reexported = exported(client, headers)
    times = [
        datetime.fromisoformat(row["completed_at"])
        for row in sorted(
            reexported["repertoire_attempts"],
            key=lambda row: row["sequence"],
        )
    ]
    assert times[1] >= times[0]
    delete_owner(client, headers)
    restored = client.post("/v1/imports", headers=headers, json=reexported)
    assert restored.status_code == 200, restored.text


def test_same_generic_key_concurrent_imports_are_independent_without_cache_insert_race(
    client: TestClient,
    db_session: Session,
) -> None:
    headers, _ = seed(client)
    headers = {**headers, "Idempotency-Key": "intentional-independent-imports"}
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)
    previous = app.dependency_overrides[get_session]

    def independent_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = independent_session
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda _: client.post(
                        "/v1/repertoires", headers=headers, json={"pgn": "1. d4 d5 *"}
                    ),
                    range(2),
                )
            )
        assert [response.status_code for response in responses] == [201, 201]
        assert responses[0].json()[0]["id"] != responses[1].json()[0]["id"]
        with Session(engine) as session:
            assert len(session.exec(select(Repertoire)).all()) == 3
            assert session.exec(select(IdempotencyRecord)).all() == []
    finally:
        app.dependency_overrides[get_session] = previous


def test_restore_keeps_legacy_archive_size_and_accumulated_repertoire_contract(
    client: TestClient,
) -> None:
    headers, _ = seed(client)
    archive = exported(client, headers)
    original = archive["repertoires"][0]
    archive["repertoires"] = [{**deepcopy(original), "id": str(uuid4())} for _ in range(20)]
    # A supported legacy field may exceed the new repertoire request-body cap.
    archive["player"]["preferences"]["legacy-large-setting"] = "x" * (MAX_IMPORT_BODY_BYTES + 1)
    delete_owner(client, headers)
    response = client.post("/v1/imports", headers=headers, json=archive)
    assert response.status_code == 200, response.text
    restored = exported(client, headers)
    assert len(restored["repertoires"]) == 20
    assert (
        restored["player"]["preferences"]["legacy-large-setting"]
        == (archive["player"]["preferences"]["legacy-large-setting"])
    )
