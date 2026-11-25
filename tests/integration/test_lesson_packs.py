from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from threading import Event
from typing import Any
from uuid import UUID

import chess
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from scan64.api.app import app, lifespan
from scan64.chess.analysis.models import EngineAnalysis
from scan64.lesson_packs.models import LessonPack, PackLesson
from scan64.lesson_packs.schema import MAX_PACK_BYTES
from scan64.providers.stockfish.pool import EnginePoolManager


class Adapter:
    async def analyze_position(self, fen: str, **kwargs: Any) -> EngineAnalysis:
        await asyncio.sleep(0)
        board = chess.Board(fen)
        best = "e2e4" if board.fullmove_number == 1 else "g1f3"
        return EngineAnalysis(
            raw_result=[{"pv": [best], "score_cp": 100}, {"pv": ["a2a3"], "score_cp": -900}],
            config=kwargs,
        )


class Pool:
    def __init__(self) -> None:
        self.acquisitions = 0

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Adapter]:
        self.acquisitions += 1
        yield Adapter()


class Manager:
    def __init__(self) -> None:
        self.batch_pool = Pool()


def document() -> dict[str, Any]:
    lesson = {
        "id": "sound",
        "title": "Develop a knight",
        "fen": chess.STARTING_FEN,
        "solution": ["e2e4", "e7e5", "g1f3"],
        "objective": "Develop pieces.",
        "hints": ["Start in the centre."],
        "explanation": "The knight develops.",
        "themes": ["development"],
    }
    return {
        "format": "scan64-lesson-pack",
        "format_version": 1,
        "id": "test-pack",
        "title": "Community study",
        "author": "Fixture author",
        "licence": "CC0-1.0",
        "version": "1.0",
        "description": "Plain text",
        "source": "Local fixture",
        "lessons": [lesson, {**lesson, "id": "unsound", "solution": ["f2f3"]}],
    }


def register(client: TestClient, player: str) -> dict[str, str]:
    response = client.post("/v1/players", json={"id": player, "display_name": player})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def finished(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get("/v1/lesson-packs/test-pack", headers=headers)
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        if result["state"] not in {"pending", "verifying"}:
            return result
        time.sleep(0.02)
    pytest.fail("Lesson pack verification did not finish")


def install(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    raw = json.dumps(document()).encode()
    response = client.post("/v1/lesson-packs", content=raw, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()["sha256"] == sha256(raw).hexdigest()
    return finished(client, headers)


def snapshot(session: Session) -> dict[str, list[tuple[Any, ...]]]:
    session.expire_all()
    return {
        table: [tuple(row) for row in session.exec(text(f'SELECT * FROM "{table}"')).all()]
        for table in SQLModel.metadata.tables
    }


def test_pack_install_play_ownership_and_uninstall(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Manager()
    monkeypatch.setattr(app.state, "engine_pool_manager", manager)
    owner = register(client, "pack-owner")
    other = register(client, "pack-other")
    pack = install(client, owner)
    assert pack["state"] == "verified"
    assert {lesson["id"]: lesson["state"] for lesson in pack["lessons"]} == {
        "sound": "verified",
        "unsound": "rejected",
    }
    rejected = next(lesson for lesson in pack["lessons"] if lesson["id"] == "unsound")
    assert rejected["failing_ply"] == 1
    assert rejected["reason"]
    assert manager.batch_pool.acquisitions == 1
    assert client.get("/v1/lesson-packs", headers=other).json() == []
    base = "/v1/lesson-packs/test-pack"
    for method, suffix in [
        ("get", ""),
        ("get", "/lessons/sound"),
        ("post", "/retry"),
        ("delete", ""),
    ]:
        response = getattr(client, method)(base + suffix, headers=other)
        missing = getattr(client, method)(
            base.replace("test-pack", "absent") + suffix, headers=other
        )
        assert response.status_code == missing.status_code == 404
        assert response.json() == missing.json()
    check = base + "/lessons/sound/check"
    assert client.post(check, json={"ply": 0, "move": "e2e4"}, headers=other).status_code == 404
    assert client.get(base + "/lessons/unsound", headers=owner).status_code == 404
    lesson_read = client.get(base + "/lessons/sound", headers=owner).json()
    assert lesson_read["author"] == "Fixture author"
    assert "hints" not in lesson_read and "explanation" not in lesson_read
    before = snapshot(db_session)
    reads = [
        f"/v1/players/pack-owner/{path}"
        for path in ("patterns", "progress", "progress/pattern-recurrence")
    ]
    responses = [client.get(path, headers=owner) for path in reads]
    assert all(response.status_code == 200 for response in responses)
    reports = [response.json() for response in responses]
    # The cache header must not let a successful move leak across owners or write a row.
    move_headers = {**owner, "Idempotency-Key": "pack-move-test"}
    hint = client.post(check, json={"ply": 0, "hint": True}, headers=move_headers)
    assert hint.status_code == 200 and hint.json()["hints"] == ["Start in the centre."]
    wrong = client.post(check, json={"ply": 0, "move": "e2e3"}, headers=move_headers)
    assert wrong.status_code == 200 and not wrong.json()["accepted"]
    first = client.post(check, json={"ply": 0, "move": "e2e4"}, headers=move_headers)
    assert first.status_code == 200, first.text
    assert first.json()["accepted"] and first.json()["reply"] == "e7e5"
    assert first.json()["ply"] == 2 and not first.json()["complete"]
    final = client.post(check, json={"ply": 2, "move": "g1f3"}, headers=move_headers)
    assert final.json()["complete"] and final.json()["explanation"] == "The knight develops."
    assert (
        client.post(
            check,
            json={"ply": 0, "move": "e2e4"},
            headers={**other, "Idempotency-Key": "pack-move-test"},
        ).status_code
        == 404
    )
    assert snapshot(db_session) == before
    assert [client.get(path, headers=owner).json() for path in reads] == reports
    assert client.delete(base, headers=owner).status_code == 204
    assert client.get(base + "/lessons/sound", headers=owner).status_code == 404
    db_session.expire_all()
    assert db_session.exec(select(LessonPack)).all() == []
    assert db_session.exec(select(PackLesson)).all() == []


def test_pack_bad_files_duplicate_and_unavailable_retry(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(client, "pack-bounds")
    monkeypatch.setattr(app.state, "engine_pool_manager", None)
    before = snapshot(db_session)
    for raw, expected in [
        (b"{", 422),
        (b'{"\\ud800":0,"\\ud800":1}', 422),
        (json.dumps({**document(), "\ud800": True}).encode(), 422),
        (b" " * (MAX_PACK_BYTES + 1), 413),
        (json.dumps({**document(), "unexpected": True}).encode(), 422),
        (json.dumps({**document(), "lessons": [document()["lessons"][0]] * 2}).encode(), 422),
    ]:
        assert client.post("/v1/lesson-packs", content=raw, headers=headers).status_code == expected
        assert snapshot(db_session) == before
    result = install(client, headers)
    assert result["state"] == "unavailable"
    assert result["lessons"] == []
    base = "/v1/lesson-packs/test-pack"
    assert client.get(base + "/lessons/sound", headers=headers).status_code == 404
    before_duplicate = snapshot(db_session)
    assert (
        client.post(
            "/v1/lesson-packs", json={**document(), "version": "2.0"}, headers=headers
        ).status_code
        == 409
    )
    assert snapshot(db_session) == before_duplicate
    monkeypatch.setattr(app.state, "engine_pool_manager", Manager())
    assert client.post(base + "/retry", headers=headers).status_code == 200
    assert finished(client, headers)["state"] == "verified"


def test_pack_archive_exclusion_and_player_deletion(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state, "engine_pool_manager", Manager())
    headers = register(client, "pack-delete")
    other = register(client, "pack-retain")
    install(client, headers)
    install(client, other)
    exported = client.post("/v1/exports", json={"player_id": "pack-delete"}, headers=headers)
    assert exported.status_code == 200, exported.text
    assert "Lesson packs are not included" in exported.json()["lesson_pack_notice"]
    assert "lesson_packs" not in exported.json() and "pack_lessons" not in exported.json()
    response = client.request(
        "DELETE", "/v1/players/pack-delete/data", json={"dry_run": True}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["affected_rows"]["lesson_packs"] == 1
    assert response.json()["affected_rows"]["pack_lessons"] == 2
    response = client.request(
        "DELETE",
        "/v1/players/pack-delete/data",
        json={"dry_run": False, "confirmation": "delete-pack-delete"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert len(db_session.exec(select(LessonPack)).all()) == 1
    assert len(db_session.exec(select(PackLesson)).all()) == 2
    assert len(client.get("/v1/lesson-packs", headers=other).json()) == 1


@pytest.mark.parametrize("delete_player", [False, True])
def test_deletion_after_pack_task_registry_teardown(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    delete_player: bool,
) -> None:
    monkeypatch.setattr(app.state, "engine_pool_manager", Manager())
    headers = register(client, "pack-shutdown")
    install(client, headers)
    with monkeypatch.context() as shutdown:
        shutdown.delattr(app.state, "lesson_pack_tasks")
        shutdown.delattr(app.state, "lesson_pack_lock")
        if delete_player:
            response = client.request(
                "DELETE",
                "/v1/players/pack-shutdown/data",
                json={"dry_run": False, "confirmation": "delete-pack-shutdown"},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            assert response.json()["affected_rows"]["lesson_packs"] == 1
        else:
            response = client.delete("/v1/lesson-packs/test-pack", headers=headers)
            assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.exec(select(LessonPack)).all() == []
    assert db_session.exec(select(PackLesson)).all() == []


@pytest.mark.parametrize("retry", [False, True])
def test_pack_enqueue_after_task_registry_teardown(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    retry: bool,
) -> None:
    headers = register(client, "pack-enqueue-shutdown")
    if retry:
        monkeypatch.setattr(app.state, "engine_pool_manager", None)
        assert install(client, headers)["state"] == "unavailable"
    manager = Manager()
    monkeypatch.setattr(app.state, "engine_pool_manager", manager)
    with monkeypatch.context() as shutdown:
        shutdown.delattr(app.state, "lesson_pack_tasks")
        shutdown.delattr(app.state, "lesson_pack_lock")
        shutdown.delattr(app.state, "engine_pool_manager")
        if retry:
            response = client.post("/v1/lesson-packs/test-pack/retry", headers=headers)
            assert response.status_code == 200, response.text
        else:
            response = client.post("/v1/lesson-packs", json=document(), headers=headers)
            assert response.status_code == 201, response.text
        assert response.json()["state"] == "pending"
    db_session.expire_all()
    assert len(db_session.exec(select(LessonPack)).all()) == 1
    assert manager.batch_pool.acquisitions == 0


@pytest.mark.asyncio
async def test_shutdown_drains_pack_tasks_without_admitting_more(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls: list[UUID] = []

    async def verify(engine: Engine, pack_id: UUID, manager: EnginePoolManager | None) -> None:
        calls.append(pack_id)
        started.set()
        try:
            await release.wait()
        finally:
            draining.set()
            await release.wait()

    monkeypatch.setattr("scan64.api.lesson_packs.verify_pack", verify)
    context = lifespan(app)
    await context.__aenter__()
    shutdown: asyncio.Task[bool | None] | None = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/players", json={"id": "pack-drain", "display_name": "Pack drain"}
            )
            assert response.status_code == 200, response.text
            headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
            response = await client.post("/v1/lesson-packs", json=document(), headers=headers)
            assert response.status_code == 201, response.text
            await asyncio.wait_for(started.wait(), timeout=5)
            shutdown = asyncio.create_task(context.__aexit__(None, None, None))
            await asyncio.wait_for(draining.wait(), timeout=5)
            assert not shutdown.done()
            response = await client.post(
                "/v1/lesson-packs", json={**document(), "id": "late-pack"}, headers=headers
            )
            assert response.status_code == 201, response.text
    finally:
        release.set()
        if shutdown is None:
            await asyncio.wait_for(context.__aexit__(None, None, None), timeout=5)
        else:
            await asyncio.wait_for(shutdown, timeout=5)
    assert len(calls) == 1


@pytest.mark.parametrize("delete_player", [False, True])
def test_uninstall_cancels_verification_and_releases_lease(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    delete_player: bool,
) -> None:
    started, released = Event(), Event()

    class BlockingAdapter(Adapter):
        async def analyze_position(self, fen: str, **kwargs: Any) -> EngineAnalysis:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("Unreachable")

    class BlockingPool(Pool):
        @asynccontextmanager
        async def acquire(self) -> AsyncIterator[Adapter]:
            self.acquisitions += 1
            try:
                yield BlockingAdapter()
            finally:
                released.set()

    manager = Manager()
    manager.batch_pool = BlockingPool()
    monkeypatch.setattr(app.state, "engine_pool_manager", manager)
    headers = register(client, "pack-cancel")
    response = client.post("/v1/lesson-packs", json=document(), headers=headers)
    assert response.status_code == 201, response.text
    assert started.wait(5)
    assert (
        client.get("/v1/lesson-packs/test-pack/lessons/sound", headers=headers).status_code == 404
    )
    if delete_player:
        response = client.request(
            "DELETE",
            "/v1/players/pack-cancel/data",
            json={"dry_run": False, "confirmation": "delete-pack-cancel"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
    else:
        assert client.delete("/v1/lesson-packs/test-pack", headers=headers).status_code == 204
    assert released.wait(5)
    db_session.expire_all()
    assert db_session.exec(select(LessonPack)).all() == []
    assert db_session.exec(select(PackLesson)).all() == []
    assert manager.batch_pool.acquisitions == 1


def test_install_authenticates_before_parsing(client: TestClient, db_session: Session) -> None:
    before = snapshot(db_session)
    response = client.post("/v1/lesson-packs", content=b"{")
    assert response.status_code == 401, response.text
    assert snapshot(db_session) == before


def test_installed_pack_limit_is_owner_scoped_and_atomic(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state, "engine_pool_manager", None)
    headers = register(client, "pack-limit")
    other = register(client, "pack-limit-other")
    for index in range(20):
        response = client.post(
            "/v1/lesson-packs",
            json={**document(), "id": f"pack-{index}"},
            headers=headers,
        )
        assert response.status_code == 201, response.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        packs = client.get("/v1/lesson-packs", headers=headers).json()
        if all(pack["state"] == "unavailable" for pack in packs):
            break
        time.sleep(0.02)
    else:
        pytest.fail("Installed packs did not finish unavailable verification")
    assert len(packs) == 20
    before = snapshot(db_session)
    response = client.post(
        "/v1/lesson-packs", json={**document(), "id": "pack-21"}, headers=headers
    )
    assert response.status_code == 409, response.text
    assert "20" in response.json()["detail"] and "uninstall" in response.json()["detail"]
    assert snapshot(db_session) == before
    assert (
        client.post(
            "/v1/lesson-packs", json={**document(), "id": "pack-21"}, headers=other
        ).status_code
        == 201
    )
    assert client.delete("/v1/lesson-packs/pack-0", headers=headers).status_code == 204
    assert (
        client.post(
            "/v1/lesson-packs", json={**document(), "id": "pack-21"}, headers=headers
        ).status_code
        == 201
    )


def test_two_installs_share_only_one_engine_and_queue_the_second(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = Event(), Event()

    class BlockingAdapter(Adapter):
        async def analyze_position(self, fen: str, **kwargs: Any) -> EngineAnalysis:
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return await super().analyze_position(fen, **kwargs)

    class TrackingPool(Pool):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0

        @asynccontextmanager
        async def acquire(self) -> AsyncIterator[Adapter]:
            self.acquisitions += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                yield BlockingAdapter()
            finally:
                self.active -= 1

    manager = Manager()
    pool = TrackingPool()
    manager.batch_pool = pool
    monkeypatch.setattr(app.state, "engine_pool_manager", manager)
    headers = register(client, "pack-queue")
    assert client.post("/v1/lesson-packs", json=document(), headers=headers).status_code == 201
    assert started.wait(5)
    response = client.post(
        "/v1/lesson-packs", json={**document(), "id": "second-pack"}, headers=headers
    )
    assert response.status_code == 201, response.text
    assert pool.acquisitions == 1 and pool.active == 1
    assert client.get("/v1/lesson-packs/second-pack", headers=headers).json()["state"] == "pending"
    assert client.get("/v1/lesson-packs/test-pack", headers=headers).json()["state"] == "verifying"
    release.set()
    assert finished(client, headers)["state"] == "verified"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        second = client.get("/v1/lesson-packs/second-pack", headers=headers).json()
        if second["state"] == "verified":
            break
        time.sleep(0.02)
    else:
        pytest.fail("Queued second pack did not finish verification")
    assert pool.acquisitions == 2 and pool.max_active == 1 and pool.active == 0
