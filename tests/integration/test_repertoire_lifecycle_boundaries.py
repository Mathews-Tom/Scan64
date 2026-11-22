from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from threading import Event, Lock
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

import scan64.api.data_lifecycle as lifecycle
import scan64.api.repertoire as repertoire_api
from scan64.api.app import app
from scan64.api.middleware import IdempotencyRecord
from scan64.api.models import Player, PlayerCredential
from scan64.api.repertoire_body import MAX_ATTEMPT_BODY_BYTES, MAX_IMPORT_BODY_BYTES
from scan64.learning.repertoire import Repertoire, RepertoireAttempt, parse_repertoire_pgn
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule, ScheduleReviewObservation
from scan64.persistence.database import get_session
from tests.integration.test_repertoire import OWNER, delete_owner, review, seed
from tests.integration.test_repertoire_security import exported


@contextmanager
def routed_client(client: TestClient, prefix: str) -> Iterator[TestClient]:
    if not prefix:
        yield client
        return
    mounted = FastAPI()
    mounted.mount(prefix, app)
    with TestClient(mounted) as scoped:
        yield scoped


@contextmanager
def independent_requests(session: Session) -> Iterator[Engine]:
    engine = session.get_bind()
    assert isinstance(engine, Engine)
    previous = app.dependency_overrides[get_session]

    def request_session() -> Iterator[Session]:
        with Session(engine) as owned_session:
            yield owned_session

    app.dependency_overrides[get_session] = request_session
    try:
        yield engine
    finally:
        app.dependency_overrides[get_session] = previous


@pytest.mark.parametrize("prefix", ["", "/mounted"])
@pytest.mark.parametrize("player_id", ["owner?literal", "owner#literal", "owner?literal#both"])
def test_delimiter_player_ids_always_reach_live_lifecycle_routes(
    client: TestClient,
    db_session: Session,
    prefix: str,
    player_id: str,
) -> None:
    with routed_client(client, prefix) as scoped:
        registration = scoped.post(
            prefix + "/v1/players",
            json={
                "id": player_id,
                "display_name": "Boundary owner",
                "preferences": {},
            },
        )
        assert registration.status_code == 200
        headers = {
            "Authorization": f"Bearer {registration.json()['access_token']}",
            "Idempotency-Key": "same-live-route-key",
        }
        imported = scoped.post(
            prefix + "/v1/repertoires", headers=headers, json={"pgn": "1. e4 e5 *"}
        )
        assert imported.status_code == 201, imported.text
        repertoire_id = imported.json()[0]["id"]
        owner_path = prefix + "/v1/players/" + quote(player_id, safe="")
        for _ in range(2):
            drilled = scoped.post(
                prefix + f"/v1/repertoires/{repertoire_id}/attempts",
                headers=headers,
                json={
                    "attempt_id": str(uuid4()),
                    "node_id": 0,
                    "submitted_move": "e2e4",
                },
            )
            assert drilled.status_code == 200
            reset = scoped.post(
                owner_path + "/progress/reset",
                headers=headers,
                json={"confirmation": "RESET PROGRESS"},
            )
            assert reset.status_code == 200, reset.text
            assert reset.json()["cleared"]["repertoire_attempts"] == 1
        archive = scoped.post(
            prefix + "/v1/exports", headers=headers, json={"player_id": player_id}
        )
        assert archive.status_code == 200 and archive.json()["repertoire_attempts"] == []
        dry = scoped.request(
            "DELETE", owner_path + "/data", headers=headers, json={"dry_run": True}
        )
        assert dry.status_code == 200 and dry.json()["dry_run"]
        deleted = scoped.request(
            "DELETE",
            owner_path + "/data",
            headers=headers,
            json={"dry_run": False, "confirmation": f"delete-{player_id}"},
        )
        assert deleted.status_code == 200 and not deleted.json()["dry_run"]
        assert (
            scoped.post(
                prefix + "/v1/exports", headers=headers, json={"player_id": player_id}
            ).status_code
            == 403
        )
    db_session.expire_all()
    assert db_session.get(Player, player_id) is None
    assert db_session.exec(select(IdempotencyRecord)).all() == []


@pytest.mark.parametrize(
    "route,limit",
    [
        ("/v1/repertoires", MAX_IMPORT_BODY_BYTES),
        (f"/v1/repertoires/{uuid4()}/attempts", MAX_ATTEMPT_BODY_BYTES),
    ],
)
def test_mounted_repertoire_routes_bound_receive_before_json_decode(
    client: TestClient,
    route: str,
    limit: int,
) -> None:
    with routed_client(client, "/mounted") as scoped:
        response = scoped.post(
            "/mounted" + route,
            content=b"x" * (limit + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


def test_confirmed_deletion_waits_for_review_before_counting_and_removing_history(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, repertoire = seed(client)
    held, deletion_started, release = Event(), Event(), Event()
    real_review_lock = repertoire_api.lock_puzzle_player
    real_delete_lock = lifecycle.lock_puzzle_player

    def hold_review(player_id: str, session: Session) -> None:
        real_review_lock(player_id, session)
        held.set()
        assert release.wait(10)

    def signal_delete(player_id: str, session: Session) -> None:
        deletion_started.set()
        real_delete_lock(player_id, session)

    monkeypatch.setattr(repertoire_api, "lock_puzzle_player", hold_review)
    monkeypatch.setattr(lifecycle, "lock_puzzle_player", signal_delete)
    with independent_requests(db_session) as engine:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending_review = pool.submit(review, client, headers, repertoire)
            try:
                assert held.wait(10)
                pending_delete = pool.submit(
                    client.request,
                    "DELETE",
                    f"/v1/players/{OWNER}/data",
                    headers=headers,
                    json={
                        "dry_run": False,
                        "confirmation": f"delete-{OWNER}",
                    },
                )
                assert deletion_started.wait(10)
            finally:
                release.set()
            assert pending_review.result(timeout=10).status_code == 200
            deleted = pending_delete.result(timeout=10)
            assert deleted.status_code == 200, deleted.text
            assert deleted.json()["affected_rows"]["repertoire_attempts"] == 1
        with Session(engine) as fresh:
            for model in (
                Player,
                Repertoire,
                RepertoireAttempt,
                ReviewSchedule,
                ScheduleReviewObservation,
            ):
                assert fresh.exec(select(model)).all() == []


@pytest.mark.parametrize("restore_source", [False, True])
def test_waiting_attempt_uses_post_fence_authorization_and_tree(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    restore_source: bool,
) -> None:
    headers, repertoire = seed(client)
    archive = exported(client, headers)
    ready, release = Event(), Event()
    real_lock = repertoire_api.lock_puzzle_player

    def pause_before_fence(player_id: str, session: Session) -> None:
        ready.set()
        assert release.wait(10)
        real_lock(player_id, session)

    monkeypatch.setattr(repertoire_api, "lock_puzzle_player", pause_before_fence)
    with independent_requests(db_session) as engine:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(review, client, headers, repertoire)
            try:
                assert ready.wait(10)
                delete_owner(client, headers)
                if restore_source:
                    title, pgn, tree = parse_repertoire_pgn("1. d4 d5 *")[0]
                    archive["repertoires"][0].update(title=title, pgn=pgn, tree=tree.model_dump())
                    restored = client.post("/v1/imports", headers=headers, json=archive)
                    assert restored.status_code == 200, restored.text
            finally:
                release.set()
            response = pending.result(timeout=10)
            if restore_source:
                assert response.status_code == 200, response.text
                assert not response.json()["success"] and response.json()["rating"] == 1
                # The result belongs to the replacement tree, not the pre-fence source.
                current = exported(client, headers)
                delete_owner(client, headers)
                assert client.post("/v1/imports", headers=headers, json=current).status_code == 200
            else:
                assert response.status_code == 401
                with Session(engine) as fresh:
                    assert fresh.exec(select(RepertoireAttempt)).all() == []


def test_waiting_old_bearer_delete_cannot_delete_recreated_owner(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, _ = seed(client)
    ready, release, first_guard = Event(), Event(), Lock()
    first = True
    real_lock = lifecycle.lock_puzzle_player

    def pause_first_delete(player_id: str, session: Session) -> None:
        nonlocal first
        with first_guard:
            pause, first = first, False
        if pause:
            # Retain the ORM credential to exercise a warm identity map explicitly.
            session.info["cached_credential"] = session.get(PlayerCredential, player_id)
            ready.set()
            assert release.wait(10)
        real_lock(player_id, session)

    monkeypatch.setattr(lifecycle, "lock_puzzle_player", pause_first_delete)
    with independent_requests(db_session):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                client.request,
                "DELETE",
                f"/v1/players/{OWNER}/data",
                headers=headers,
                json={
                    "dry_run": False,
                    "confirmation": f"delete-{OWNER}",
                },
            )
            try:
                assert ready.wait(10)
                delete_owner(client, headers)
                replacement = client.post(
                    "/v1/players",
                    json={
                        "id": OWNER,
                        "display_name": "Replacement owner",
                        "preferences": {},
                    },
                )
                assert replacement.status_code == 200
                new_headers = {"Authorization": f"Bearer {replacement.json()['access_token']}"}
                imported = client.post(
                    "/v1/repertoires", headers=new_headers, json={"pgn": "1. d4 d5 *"}
                )
                assert imported.status_code == 201
            finally:
                release.set()
            assert pending.result(timeout=10).status_code == 403
        assert len(client.get("/v1/repertoires", headers=new_headers).json()) == 1


def test_delete_dry_run_does_not_take_a_write_fence(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, _ = seed(client)

    def forbidden_fence(player_id: str, session: Session) -> None:
        raise AssertionError("Dry-run deletion must remain read-only")

    monkeypatch.setattr(lifecycle, "lock_puzzle_player", forbidden_fence)
    response = client.request(
        "DELETE", f"/v1/players/{OWNER}/data", headers=headers, json={"dry_run": True}
    )
    assert response.status_code == 200 and response.json()["dry_run"]


def test_mixed_offset_archive_timestamps_persist_as_same_utc_instants(
    client: TestClient,
) -> None:
    headers, repertoire = seed(client)
    assert review(client, headers, repertoire).status_code == 200
    assert review(client, headers, repertoire, submitted_move="a2a3").status_code == 200
    archive = exported(client, headers)
    offsets = iter(
        [
            timedelta(hours=5, minutes=30),
            timedelta(hours=-4),
            timedelta(hours=9),
            timedelta(minutes=-30),
        ]
        * 4
    )
    time_fields = (
        ("repertoires", ("created_at",)),
        ("repertoire_attempts", ("completed_at",)),
        ("review_schedules", ("next_review_at", "last_reviewed_at")),
        ("schedule_review_observations", ("reviewed_at",)),
    )

    def instant_at(raw: str) -> datetime:
        value = datetime.fromisoformat(raw)
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    expected: dict[tuple[str, str, str], datetime] = {}
    for group, fields in time_fields:
        for row in archive[group]:
            for field in fields:
                instant = instant_at(row[field])
                identity = row.get("id", row.get("item_id"))
                expected[group, identity, field] = instant
                row[field] = instant.astimezone(timezone(next(offsets))).isoformat()
    delete_owner(client, headers)
    restored = client.post("/v1/imports", headers=headers, json=archive)
    assert restored.status_code == 200, restored.text
    current = exported(client, headers)
    actual: dict[tuple[str, str, str], datetime] = {}
    for group, fields in time_fields:
        for row in current[group]:
            for field in fields:
                identity = row.get("id", row.get("item_id"))
                actual[group, identity, field] = instant_at(row[field])
    assert actual == expected
    delete_owner(client, headers)
    assert client.post("/v1/imports", headers=headers, json=current).status_code == 200
    assert review(client, headers, repertoire).status_code == 200


@pytest.mark.parametrize("result", ["invalid", "", "0.5-0.5"])
def test_invalid_result_metadata_rejects_atomically(
    client: TestClient,
    db_session: Session,
    result: str,
) -> None:
    headers, _ = seed(client)
    response = client.post(
        "/v1/repertoires", headers=headers, json={"pgn": f'[Result "{result}"]\n\n1. e4 *'}
    )
    assert response.status_code == 422 and "Result" in response.json()["detail"]
    assert len(db_session.exec(select(Repertoire)).all()) == 1


def test_each_supported_result_metadata_value_remains_restorable(client: TestClient) -> None:
    headers, _ = seed(client)
    for result in ("1-0", "0-1", "1/2-1/2", "*"):
        response = client.post(
            "/v1/repertoires",
            headers=headers,
            json={"pgn": f'[Result "{result}"]\n\n1. e4 {result}'},
        )
        assert response.status_code == 201, response.text
    archive = exported(client, headers)
    delete_owner(client, headers)
    response = client.post("/v1/imports", headers=headers, json=archive)
    assert response.status_code == 200, response.text
    assert exported(client, headers)["repertoires"] == archive["repertoires"]
