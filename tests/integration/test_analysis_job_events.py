"""Consumer-visible snapshots from the real committed analysis-job producer."""

import asyncio
import json
from collections.abc import Iterator
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, create_engine, select

import scan64.api.games as games_api
import scan64.chess.analysis.jobs as jobs
from scan64.api.data_lifecycle import DeletionRequest, delete_player_data
from scan64.api.desktop_security import add_desktop_security
from scan64.api.models import DeletionAudit, Player, PlayerCredential, issue_player_token
from scan64.chess.analysis import notifications
from scan64.chess.analysis.admission import AdmissionConfig, AdmissionController
from scan64.chess.analysis.models import AnalysisJob, PersistedLessonOpportunity
from scan64.chess.analysis.recovery import recover_analysis_jobs
from scan64.chess.games.models import Game
from scan64.explanations.settings import OPERATOR_LLM_CONFIG, OwnerExplanationSettings
from scan64.persistence import database
from scan64.providers.llm import local_ollama
from tests.streaming import disconnect_stream

from .test_live_detector_coverage import (
    _FIXTURES,
    _FixtureFastPassOrchestrator,
    _FixtureFocusedPassOrchestrator,
    _registry,
)


def register(session: Session, player_id: str) -> dict[str, str]:
    token, token_hash = issue_player_token()
    session.add(Player(id=player_id))
    session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


def create_job(
    session: Session, *, status: str = "pending", valid: bool = True, owner: str = "push-owner"
) -> UUID:
    fixture = _FIXTURES[0]
    game = Game(
        pgn="",
        headers={"FEN": fixture["initial_fen"]},
        moves=fixture["moves"],
        owner_player_id=owner,
        owner_color="white" if valid else None,
    )
    session.add(game)
    session.commit()
    job = AnalysisJob(game_id=game.id, status=status)
    session.add(job)
    session.commit()
    return job.id


def request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        }
    )


def read_status(job_id: UUID) -> str:
    with Session(database.engine) as session:
        job = session.get(AnalysisJob, job_id)
        assert job is not None
        return job.status


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_current_terminal_and_access_boundaries(db_session: Session, terminal: str) -> None:
    owner = register(db_session, "push-owner")
    other = register(db_session, "other-player")
    job_id = create_job(db_session, status=terminal)
    app = FastAPI()
    app.include_router(games_api.router)
    add_desktop_security(app, "push-shell")

    def sessions() -> Iterator[Session]:
        with Session(database.engine) as session:
            yield session

    app.dependency_overrides[database.get_session] = sessions
    path = f"/v1/analysis-jobs/{job_id}/events"
    shell = {"X-Scan64-Shell-Token": "push-shell"}
    with TestClient(app) as client:
        assert client.get(path, headers=owner).status_code == 401
        assert client.get(path, headers=shell).status_code == 401
        invalid = {**shell, "Authorization": "Bearer invalid"}
        assert client.get(path, headers=invalid).status_code == 401
        assert client.get(path, headers={**shell, **other}).status_code == 404
        assert (
            client.get(
                f"/v1/analysis-jobs/{uuid4()}/events", headers={**shell, **owner}
            ).status_code
            == 404
        )
        response = client.get(path, headers={**shell, **owner})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    snapshots = [json.loads(line) for line in response.text.splitlines()]
    assert len(snapshots) == 1
    assert snapshots[0]["id"] == str(job_id)
    assert snapshots[0]["status"] == terminal
    assert set(snapshots[0]) == {"id", "game_id", "status"}
    assert not notifications._listeners


@pytest.mark.asyncio
@pytest.mark.parametrize("generation_changed", [False, True])
async def test_queued_analysis_uses_only_the_game_snapshot_owners_consent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, generation_changed: bool
) -> None:
    owner = "push-owner"
    register(db_session, owner)
    db_session.add(OwnerExplanationSettings(player_id=owner, model="test-local-model"))
    db_session.commit()
    job_id = create_job(db_session, owner=owner)
    monkeypatch.delenv(OPERATOR_LLM_CONFIG, raising=False)
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _FixtureFastPassOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FixtureFocusedPassOrchestrator)
    original = jobs.run_analysis_for_game

    async def analyse(*args: object, **kwargs: object) -> None:
        if generation_changed:
            with Session(database.engine) as writer:
                writer.add(DeletionAudit(id="analysis-epoch", player_id=owner))
                writer.commit()
        await original(*args, registry=_registry(), **kwargs)  # type: ignore[arg-type]

    requests: list[str] = []

    async def unavailable(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(503)

    monkeypatch.setattr(jobs, "run_analysis_for_game", analyse)
    monkeypatch.setattr(
        local_ollama.httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(unavailable)
    )
    await jobs.execute_analysis_job_async(job_id)

    assert read_status(job_id) == "completed"
    assert db_session.exec(select(PersistedLessonOpportunity)).first() is not None
    assert set(requests) == (set() if generation_changed else {"/api/tags"})


@pytest.mark.asyncio
@pytest.mark.parametrize("quota", [0, 50])
@pytest.mark.parametrize("recover", [False, True])
async def test_real_admitted_and_recovered_producer_streams_committed_success(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, quota: int, recover: bool
) -> None:
    headers = register(db_session, "push-owner")
    job_id = create_job(db_session, status="running" if recover else "pending")
    controller = AdmissionController(AdmissionConfig(daily_quota_games=quota))
    monkeypatch.setattr(jobs, "admission_controller", controller)
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _FixtureFastPassOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FixtureFocusedPassOrchestrator)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = jobs.run_analysis_for_game

    async def gated_analysis(*args: object, **kwargs: object) -> None:
        entered.set()
        await release.wait()
        await original(*args, registry=_registry(), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(jobs, "run_analysis_for_game", gated_analysis)
    response = await games_api.analysis_job_events(
        job_id, request(headers), Session(database.engine)
    )
    body = response.body_iterator
    try:
        assert json.loads(await anext(body))["status"] == ("running" if recover else "pending")
        if recover:
            await recover_analysis_jobs(None)
        else:
            await jobs.submit_analysis_job("push-owner", job_id)
        await asyncio.wait_for(entered.wait(), 5)
        assert read_status(job_id) == "running"
        if not recover:
            assert json.loads(await asyncio.wait_for(anext(body), 5))["status"] == "running"
        release.set()
        terminal = json.loads(await asyncio.wait_for(anext(body), 5))
        assert terminal["status"] == "completed"
        assert read_status(job_id) == "completed"
        with Session(database.engine) as session:
            assert session.exec(select(PersistedLessonOpportunity)).first() is not None
        with pytest.raises(StopAsyncIteration):
            await anext(body)
    finally:
        release.set()
        await body.aclose()
        await controller.stop()
    assert not notifications._listeners


@pytest.mark.asyncio
async def test_real_analysis_validation_failure_is_terminal_and_committed(
    db_session: Session,
) -> None:
    headers = register(db_session, "push-owner")
    job_id = create_job(db_session, valid=False)
    response = await games_api.analysis_job_events(
        job_id, request(headers), Session(database.engine)
    )
    body = response.body_iterator
    assert json.loads(await anext(body))["status"] == "pending"
    await jobs.execute_analysis_job_async(job_id)
    terminal = json.loads(await asyncio.wait_for(anext(body), 5))
    assert terminal["status"] == "failed"
    with Session(database.engine) as session:
        failed = session.get(AnalysisJob, job_id)
        assert failed is not None
        assert failed.status == "failed"
        assert "owner_color" in (failed.error or "")
    with pytest.raises(StopAsyncIteration):
        await anext(body)
    assert not notifications._listeners


@pytest.mark.asyncio
async def test_missing_game_failure_notifies_existing_subscription(db_session: Session) -> None:
    job = AnalysisJob(game_id=uuid4())
    db_session.add(job)
    db_session.commit()
    job_id = job.id
    listener = notifications.AnalysisJobSubscription(database.engine, job_id, "push-owner")
    try:
        await jobs.execute_analysis_job_async(job_id)
        assert await asyncio.wait_for(listener.wait(), 5)
        with Session(database.engine) as session:
            failed = session.get(AnalysisJob, job_id)
            assert failed is not None
            assert failed.status == "failed" and failed.error == "Game not found"
    finally:
        listener.close()
    assert not notifications._listeners


@pytest.mark.asyncio
async def test_final_commit_failure_publishes_failed_not_completed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session, "push-owner")
    job_id = create_job(db_session)
    with Session(database.engine) as session:
        job = session.get(AnalysisJob, job_id)
        assert job is not None
        session.add(AnalysisJob(game_id=job.game_id, status="completed"))
        session.commit()
    original = jobs._JobSession.commit
    fail_once = True

    def commit(session: jobs._JobSession) -> None:
        nonlocal fail_once
        if fail_once and any(
            isinstance(row, AnalysisJob) and row.status == "completed"
            for row in session.identity_map.values()
        ):
            fail_once = False
            raise RuntimeError("commit rejected")
        original(session)

    monkeypatch.setattr(jobs._JobSession, "commit", commit)
    response = await games_api.analysis_job_events(
        job_id, request(headers), Session(database.engine)
    )
    body = response.body_iterator
    assert json.loads(await anext(body))["status"] == "pending"
    await jobs.execute_analysis_job_async(job_id)
    assert json.loads(await asyncio.wait_for(anext(body), 5))["status"] == "failed"
    assert read_status(job_id) == "failed"
    with pytest.raises(StopAsyncIteration):
        await anext(body)
    assert not notifications._listeners


@pytest.mark.asyncio
async def test_registration_race_reads_fresh_terminal_snapshot(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session, "push-owner")
    job_id = create_job(db_session)

    class CompletingSubscription(notifications.AnalysisJobSubscription):
        def __init__(self, engine: Engine, subscribed_id: UUID, player_id: str) -> None:
            super().__init__(engine, subscribed_id, player_id)
            with Session(engine) as session:
                job = session.get(AnalysisJob, subscribed_id)
                assert job is not None
                job.status = "completed"
                session.add(job)
                session.commit()
            notifications.notify_analysis_job(engine, subscribed_id)

    monkeypatch.setattr(games_api, "AnalysisJobSubscription", CompletingSubscription)
    response = await games_api.analysis_job_events(
        job_id, request(headers), Session(database.engine)
    )
    body = response.body_iterator
    assert json.loads(await anext(body))["status"] == "completed"
    with pytest.raises(StopAsyncIteration):
        await anext(body)
    assert not notifications._listeners


@pytest.mark.asyncio
async def test_coalescing_thread_delivery_engine_job_isolation_and_shutdown(
    db_session: Session,
) -> None:
    job_id = uuid4()
    foreign_engine = create_engine("sqlite://")
    listener = notifications.AnalysisJobSubscription(database.engine, job_id, "push-owner")
    foreign_job = notifications.AnalysisJobSubscription(database.engine, uuid4(), "other-player")
    foreign_db = notifications.AnalysisJobSubscription(foreign_engine, job_id, "push-owner")
    try:

        def burst() -> None:
            for _ in range(1000):
                notifications.notify_analysis_job(database.engine, job_id)

        await asyncio.to_thread(burst)
        assert await asyncio.wait_for(listener.wait(), 5)
        assert not listener._event.is_set()
        assert not foreign_job._pending and not foreign_db._pending
        notifications.close_analysis_subscriptions(database.engine)
        assert not await asyncio.wait_for(listener.wait(), 5)
        assert not await asyncio.wait_for(foreign_job.wait(), 5)
        assert foreign_db._active
    finally:
        listener.close()
        foreign_job.close()
        foreign_db.close()
        foreign_engine.dispose()
    assert not notifications._listeners


@pytest.mark.asyncio
async def test_analysis_stream_capacity_is_scoped_and_reclaimed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifications, "_MAX_SUBSCRIPTIONS", 2)
    monkeypatch.setattr(notifications, "_MAX_SUBSCRIPTIONS_PER_PLAYER", 1)
    owner, peer, third = (register(db_session, name) for name in ("push-owner", "peer", "third"))
    job_id, other_job = create_job(db_session), create_job(db_session)
    peer_job = create_job(db_session, owner="peer")
    third_job = create_job(db_session, owner="third")

    async def open_stream(job: UUID, headers: dict[str, str]):
        response = await games_api.analysis_job_events(
            job, request(headers), Session(database.engine)
        )
        return response.body_iterator

    first = await open_stream(job_id, owner)
    try:
        assert json.loads(await anext(first))["status"] == "pending"
        with pytest.raises(HTTPException) as per_player:
            await open_stream(other_job, owner)
        assert per_player.value.status_code == 429
        second = await open_stream(peer_job, peer)
        try:
            assert json.loads(await anext(second))["status"] == "pending"
            with pytest.raises(HTTPException) as process_limit:
                await open_stream(third_job, third)
            assert process_limit.value.status_code == 429
            await first.aclose()
            replacement = await open_stream(third_job, third)
            try:
                assert json.loads(await anext(replacement))["status"] == "pending"
            finally:
                await replacement.aclose()
        finally:
            await second.aclose()
    finally:
        await first.aclose()


@pytest.mark.asyncio
async def test_committed_player_deletion_closes_only_their_streams(db_session: Session) -> None:
    owner = register(db_session, "push-owner")
    peer = register(db_session, "peer")
    job_id = create_job(db_session)
    peer_job = create_job(db_session, owner="peer")
    removed = await games_api.analysis_job_events(job_id, request(owner), Session(database.engine))
    kept = await games_api.analysis_job_events(peer_job, request(peer), Session(database.engine))
    try:
        assert json.loads(await anext(removed.body_iterator))["status"] == "pending"
        assert json.loads(await anext(kept.body_iterator))["status"] == "pending"
        delete_player_data(
            "push-owner",
            request(owner),
            DeletionRequest(dry_run=False, confirmation="delete-push-owner"),
            db_session,
        )
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(removed.body_iterator), 2)

        unrelated = db_session.get(AnalysisJob, peer_job)
        assert unrelated is not None
        unrelated.status = "completed"
        db_session.add(unrelated)
        db_session.commit()
        notifications.notify_analysis_job(database.engine, peer_job)
        terminal = await asyncio.wait_for(anext(kept.body_iterator), 2)
        assert json.loads(terminal)["status"] == "completed"
    finally:
        await removed.body_iterator.aclose()
        await kept.body_iterator.aclose()


@pytest.mark.asyncio
async def test_disconnected_and_cancelled_streams_release_listener(db_session: Session) -> None:
    headers = register(db_session, "push-owner")
    job_id = create_job(db_session)
    for cancel_wait in (False, True):
        response = await games_api.analysis_job_events(
            job_id, request(headers), Session(database.engine)
        )
        body = response.body_iterator
        assert json.loads(await anext(body))["status"] == "pending"
        if cancel_wait:
            waiting = asyncio.create_task(anext(body))
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        else:
            await body.aclose()
        assert not notifications._listeners
    assert read_status(job_id) == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("before_body", [False, True])
async def test_asgi_disconnect_releases_analysis_listener_before_or_during_body(
    db_session: Session, before_body: bool
) -> None:
    headers = register(db_session, "push-owner")
    job_id = create_job(db_session)
    app = FastAPI()
    app.include_router(games_api.router)
    messages = await disconnect_stream(
        app, f"/v1/analysis-jobs/{job_id}/events", headers, before_body=before_body
    )
    assert messages[0]["status"] == 200
    if not before_body:
        assert json.loads(messages[1]["body"])["status"] == "pending"
    assert not notifications._listeners
    assert read_status(job_id) == "pending"


@pytest.mark.asyncio
async def test_stream_has_no_open_snapshot_session_while_waiting(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session, "push-owner")
    job_id = create_job(db_session)
    snapshots: list[Session] = []

    class SnapshotSession(Session):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            snapshots.append(self)

    monkeypatch.setattr(games_api, "Session", SnapshotSession)
    dependency_session = Session(database.engine)
    response = await games_api.analysis_job_events(job_id, request(headers), dependency_session)
    body = response.body_iterator
    assert json.loads(await anext(body))["status"] == "pending"
    waiting = asyncio.create_task(anext(body))
    try:
        await asyncio.sleep(0)
        assert not dependency_session.in_transaction()
        assert snapshots and all(not session.in_transaction() for session in snapshots)
        with Session(database.engine) as session:
            job = session.get(AnalysisJob, job_id)
            assert job is not None
            job.status = "completed"
            session.add(job)
            session.commit()
        notifications.notify_analysis_job(database.engine, job_id)
        assert json.loads(await asyncio.wait_for(waiting, 5))["status"] == "completed"
        assert all(not session.in_transaction() for session in snapshots)
        with pytest.raises(StopAsyncIteration):
            await anext(body)
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await body.aclose()
    assert not notifications._listeners


@pytest.mark.asyncio
async def test_actual_producer_cancellation_remains_recoverable_and_shutdown_closes_stream(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session, "push-owner")
    job_id = create_job(db_session)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = jobs.run_analysis_for_game
    controller = AdmissionController(AdmissionConfig(daily_quota_games=50))
    monkeypatch.setattr(jobs, "admission_controller", controller)

    async def paused_analysis(*args: object, **kwargs: object) -> None:
        entered.set()
        await release.wait()
        await original(*args, registry=_registry(), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(jobs, "run_analysis_for_game", paused_analysis)
    response = await games_api.analysis_job_events(
        job_id, request(headers), Session(database.engine)
    )
    body = response.body_iterator
    try:
        assert json.loads(await anext(body))["status"] == "pending"
        await jobs.submit_analysis_job("push-owner", job_id)
        await asyncio.wait_for(entered.wait(), 5)
        assert json.loads(await asyncio.wait_for(anext(body), 5))["status"] == "running"
        notifications.close_analysis_subscriptions(database.engine)
        await controller.stop()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(body), 5)
        # There is deliberately no analysis cancellation status/endpoint.
        # Shutdown interruption keeps the durable job eligible for recovery.
        assert read_status(job_id) == "running"
    finally:
        await controller.stop()
        await body.aclose()
    assert not notifications._listeners
