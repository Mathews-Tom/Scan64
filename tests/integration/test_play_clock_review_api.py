from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

import scan64.api.play as play_api
from scan64.api.app import app
from scan64.api.models import Player
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import PlayClockRuntime
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.pgn import build_pgn
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.providers.maia.config import MaiaCheckpoint, MaiaConfig
from tests.integration.test_play_clocks import FrozenTime, ScriptedOpponent, register, seed_clock
from tests.integration.test_play_clocks import frozen_time as frozen_time


@pytest.fixture(autouse=True)
def isolate_review_api(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    # These tests own every clock transition; no lifespan scheduler may race it.
    monkeypatch.setattr(app.state, "play_clock_runtime", None, raising=False)
    monkeypatch.setitem(app.dependency_overrides, play_api.get_session, lambda: db_session)
    monkeypatch.setitem(
        app.dependency_overrides, play_api.get_opponent_provider, lambda: ScriptedOpponent()
    )


@pytest.fixture(name="client")
def scheduler_free_client() -> Iterator[TestClient]:
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()


async def _wait_for_read_waiter(
    pending: asyncio.Task[httpx.Response], entered: asyncio.Event
) -> None:
    entry = asyncio.create_task(entered.wait())
    try:
        done, _ = await asyncio.wait(
            (pending, entry), timeout=2, return_when=asyncio.FIRST_COMPLETED
        )
        assert entry in done, "GET completed or timed out before entering its change wait"
    finally:
        entry.cancel()
        await asyncio.gather(entry, return_exceptions=True)


@pytest.mark.parametrize("strength", ["not-a-rating", "1500.5", "NaN", ""])
def test_maia_creation_rejects_invalid_strength_before_clock_writes(
    client: TestClient, db_session: Session, strength: str
) -> None:
    auth = register(db_session)
    response = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "alice",
            "opponent_config": {"provider": "maia", "strength": strength},
            "clock_config": {"initial_seconds": 60, "increment_seconds": 2},
        },
    )
    assert response.status_code == 422, response.text
    assert db_session.exec(select(PlaySession)).all() == []
    assert db_session.exec(select(PlayClock)).all() == []
    assert db_session.exec(select(Game)).all() == []


@pytest.mark.parametrize("strength", ["-100", "0", "1500", "5000"])
def test_maia_creation_keeps_existing_integer_rating_coverage(
    client: TestClient, db_session: Session, frozen_time: FrozenTime, strength: str
) -> None:
    auth = register(db_session)
    response = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "alice",
            "opponent_config": {"provider": "maia", "strength": strength},
            "clock_config": {"initial_seconds": 60, "increment_seconds": 2},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["opponent_config"]["strength"] == strength
    assert response.json()["clock"]["version"] == 0


@pytest.mark.parametrize("expected_version, status", [(0, 400), (1, 409)])
def test_opponent_command_maps_bad_legacy_rating_without_masking_clock_conflict(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    expected_version: int,
    status: int,
) -> None:
    auth = register(db_session)
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))
    play.opponent_config = {**play.opponent_config, "provider": "maia", "strength": "invalid"}
    db_session.add(play)
    db_session.commit()
    session_id, game_id = play.id, game.id
    response = client.post(
        f"/v1/play-sessions/{session_id}/opponent-move",
        headers=auth,
        json={"expected_clock_version": expected_version},
    )
    assert response.status_code == status, response.text
    db_session.expire_all()
    assert db_session.get(Game, game_id).moves == ["e2e4"]
    assert db_session.get(PlaySession, session_id).status == "active"
    assert db_session.get(PlayClock, session_id).version == clock.version == 0


@pytest.mark.parametrize("command", ["moves", "resume", "opponent-move"])
def test_maia_runtime_paths_are_not_returned_to_game_owners(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    command: str,
) -> None:
    auth = register(db_session)
    play, game, _ = seed_clock(
        db_session, frozen_time, moves=() if command == "moves" else ("e2e4",)
    )
    play.opponent_config = {**play.opponent_config, "provider": "maia", "strength": "1500"}
    db_session.add(play)
    db_session.commit()
    session_id, game_id = play.id, game.id
    config = MaiaConfig(
        binary_path=tmp_path / "operator-private" / "missing-maia",
        checkpoints=(MaiaCheckpoint(1500, tmp_path / "operator-private" / "weights.pb.gz"),),
    )
    monkeypatch.setattr(PlaySessionService, "configured_maia", lambda self: config)
    response = client.post(
        f"/v1/play-sessions/{session_id}/{command}",
        headers=auth,
        json={
            "expected_clock_version": 0,
            **({"move": "e2e4"} if command == "moves" else {}),
        },
    )
    assert response.status_code == 500, response.text
    assert str(tmp_path) not in response.text
    assert "missing-maia" not in response.text
    assert str(config.binary_path) in caplog.text
    db_session.expire_all()
    assert db_session.get(Game, game_id).moves == ["e2e4"]
    assert db_session.get(PlaySession, session_id).status == "active"
    assert db_session.get(PlayClock, session_id).version == (1 if command == "moves" else 0)


@pytest.mark.parametrize(
    "advance_us, status", [(59_999_500, 200), (60_000_000, 503), (60_000_500, 503)]
)
def test_get_uses_exact_deadline_instead_of_rounded_balance(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    advance_us: int,
    status: int,
) -> None:
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time)
    session_id = play.id
    frozen_time.now += timedelta(microseconds=advance_us)
    response = client.get(f"/v1/play-sessions/{session_id}", headers=auth)
    assert response.status_code == status, response.text
    if status == 200:
        data = response.json()
        assert data["status"] == "active"
        assert data["clock"]["white_remaining_ms"] == 0
        assert data["clock"]["terminal_reason"] is None
    else:
        assert "expiration is pending" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(PlaySession, session_id).status == "active"
    assert db_session.get(PlayClock, session_id).version == 0


@pytest.mark.parametrize("token", ["-1", "not-a-version", "1.5"])
def test_clock_change_query_requires_nonnegative_integer(
    client: TestClient, db_session: Session, frozen_time: FrozenTime, token: str
) -> None:
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time)
    response = client.get(
        f"/v1/play-sessions/{play.id}", headers=auth, params={"after_clock_version": token}
    )
    assert response.status_code == 422, response.text


def test_clock_change_query_preserves_owned_authorization(
    client: TestClient, db_session: Session, frozen_time: FrozenTime
) -> None:
    register(db_session)
    other_auth = register(db_session, "other")
    play, _, _ = seed_clock(db_session, frozen_time)
    response = client.get(
        f"/v1/play-sessions/{play.id}", headers=other_auth, params={"after_clock_version": 0}
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_clock_change_read_waits_for_real_opponent_commit_not_unrelated_notifications(
    db_session: Session, frozen_time: FrozenTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = register(db_session)
    play, game, _ = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id, game_id = play.id, game.id
    db_session.rollback()
    runtime = PlayClockRuntime(db_session.get_bind())
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime)
    opponent = ScriptedOpponent()
    monkeypatch.setitem(app.dependency_overrides, play_api.get_opponent_provider, lambda: opponent)
    entered, reentered = asyncio.Event(), asyncio.Event()
    original_wait = runtime.wait_for_change
    waits = 0

    async def observe_wait(observed: asyncio.Event) -> bool:
        nonlocal waits
        waits += 1
        (entered if waits == 1 else reentered).set()
        return await original_wait(observed)

    monkeypatch.setattr(runtime, "wait_for_change", observe_wait)
    pending = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            pending = asyncio.create_task(
                http.get(
                    f"/v1/play-sessions/{session_id}",
                    headers=auth,
                    params={"after_clock_version": 0},
                )
            )
            await _wait_for_read_waiter(pending, entered)
            assert opponent.contexts == []
            runtime.notify()
            await _wait_for_read_waiter(pending, reentered)
            assert not pending.done()
            committed = await asyncio.wait_for(
                http.post(
                    f"/v1/play-sessions/{session_id}/opponent-move",
                    headers=auth,
                    json={"expected_clock_version": 0},
                ),
                timeout=2,
            )
            assert committed.status_code == 200, committed.text
            response = await asyncio.wait_for(pending, timeout=2)
            assert response.status_code == 200, response.text
            assert response.json()["clock"]["version"] == 1
            assert response.json()["clock"]["active_color"] == "white"
            assert "e5" in response.json()["pgn"]
            assert len(opponent.contexts) == 1
            with Session(db_session.get_bind()) as reader:
                assert reader.get(Game, game_id).moves == ["e2e4", "e7e5"]
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await runtime.stop()


@pytest.mark.asyncio
async def test_clock_change_listener_precedes_snapshot_that_races_a_terminal_commit(
    db_session: Session, frozen_time: FrozenTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time)
    session_id = play.id
    db_session.rollback()
    engine = db_session.get_bind()
    runtime = PlayClockRuntime(engine)
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime)
    initial_event = runtime.change_event
    original_read = play_api.read_play_session
    original_wait = runtime.wait_for_change
    read_versions: list[int] = []
    observed_events: list[asyncio.Event] = []

    def read_then_commit(owned, reader, opponent_move=None):
        snapshot = original_read(owned, reader, opponent_move)
        read_versions.append(snapshot.clock.version)
        if len(read_versions) == 1:
            with Session(engine) as writer:
                PlaySessionService(writer, ScriptedOpponent(), clock_runtime=runtime).resign(
                    session_id
                )
        return snapshot

    async def observe_wait(observed: asyncio.Event) -> bool:
        observed_events.append(observed)
        return await original_wait(observed)

    monkeypatch.setattr(play_api, "read_play_session", read_then_commit)
    monkeypatch.setattr(runtime, "wait_for_change", observe_wait)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await asyncio.wait_for(
                http.get(
                    f"/v1/play-sessions/{session_id}",
                    headers=auth,
                    params={"after_clock_version": 0},
                ),
                timeout=2,
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        assert response.json()["clock"]["version"] == 1
        assert read_versions == [0, 1]
        assert observed_events == [initial_event]
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["timeout", "shutdown"])
async def test_clock_change_read_returns_fresh_snapshot_at_bounded_wait_end_without_writes(
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
) -> None:
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    db_session.rollback()
    engine = db_session.get_bind()
    runtime = PlayClockRuntime(engine)
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime)
    monkeypatch.setattr(play_api, "CLOCK_CHANGE_WAIT_SECONDS", 0.05)
    if interruption == "shutdown":
        await runtime.stop()
    reads = 0
    writes: list[str] = []
    original_read = play_api.read_play_session

    def count_read(owned, reader, opponent_move=None):
        nonlocal reads
        reads += 1
        return original_read(owned, reader, opponent_move)

    def capture_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    monkeypatch.setattr(play_api, "read_play_session", count_read)
    event.listen(engine, "before_cursor_execute", capture_write)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await asyncio.wait_for(
                http.get(
                    f"/v1/play-sessions/{session_id}",
                    headers=auth,
                    params={"after_clock_version": 0},
                ),
                timeout=2,
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "active"
        assert response.json()["clock"]["version"] == 0
        assert reads == 2
        assert writes == []
    finally:
        event.remove(engine, "before_cursor_execute", capture_write)
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["changed-version", "untimed", "completed", "missing-runtime"])
async def test_clock_change_query_does_not_wait_when_no_matching_live_clock(
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    auth = register(db_session)
    play, _, clock = seed_clock(db_session, frozen_time)
    session_id = play.id
    if state == "untimed":
        db_session.delete(clock)
        play.clock_config = None
        db_session.add(play)
        db_session.commit()
    elif state == "completed":
        PlaySessionService(db_session, ScriptedOpponent()).resign(session_id)
    db_session.rollback()
    runtime = PlayClockRuntime(db_session.get_bind())
    monkeypatch.setattr(
        app.state, "play_clock_runtime", None if state == "missing-runtime" else runtime
    )

    async def unexpected_wait(observed: asyncio.Event) -> bool:
        raise AssertionError("No matching live clock should wait")

    monkeypatch.setattr(runtime, "wait_for_change", unexpected_wait)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await asyncio.wait_for(
                http.get(
                    f"/v1/play-sessions/{session_id}",
                    headers=auth,
                    params={"after_clock_version": 1 if state == "changed-version" else 0},
                ),
                timeout=2,
            )
        assert response.status_code == 200, response.text
    finally:
        await runtime.stop()


def _continuation(
    client: TestClient, db: Session, auth: dict[str, str], moves: list[str]
) -> tuple[UUID, UUID]:
    source = Game(pgn="", moves=moves, owner_player_id="alice", owner_color="white")
    source.pgn = build_pgn(source)
    db.add(source)
    db.commit()
    created = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "alice",
            "game_id": str(source.id),
            "clock_config": {"initial_seconds": 60, "increment_seconds": 2},
        },
    )
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["opponent_config"]["initial_ply"] == str(len(moves))
    assert data["clock"]["version"] == 0
    return UUID(data["id"]), UUID(data["game_id"])


def _export_then_delete(client: TestClient, auth: dict[str, str]) -> dict:
    exported = client.post("/v1/exports", headers=auth, json={"player_id": "alice"})
    assert exported.status_code == 200, exported.text
    deleted = client.request(
        "DELETE",
        "/v1/players/alice/data",
        headers=auth,
        json={"dry_run": False, "confirmation": "delete-alice"},
    )
    assert deleted.status_code == 200, deleted.text
    return exported.json()


@pytest.mark.parametrize(
    "tamper",
    [
        "none",
        "inflated-inactive-balance",
        "negative-boundary",
        "past-replay-boundary",
        "fractional-boundary",
        "nonnumeric-boundary",
        "oversized-boundary",
        "illegal-source-prefix",
        "excess-session-version",
    ],
)
def test_continuation_archive_validates_timed_boundary_and_full_legal_replay(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    tamper: str,
) -> None:
    auth = register(db_session)
    session_id, game_id = _continuation(client, db_session, auth, ["e2e4", "e7e5"])
    archive = _export_then_delete(client, auth)
    play = next(play for play in archive["play_sessions"] if play["id"] == str(session_id))
    game = next(game for game in archive["games"] if game["id"] == str(game_id))
    clock = archive["play_clocks"][0]
    if tamper == "inflated-inactive-balance":
        # Black's source-game move cannot earn an increment in the new session.
        clock["black_remaining_ms"] = 62_000
    elif tamper == "negative-boundary":
        play["opponent_config"]["initial_ply"] = "-1"
    elif tamper == "past-replay-boundary":
        play["opponent_config"]["initial_ply"] = "3"
    elif tamper == "fractional-boundary":
        play["opponent_config"]["initial_ply"] = "1.5"
    elif tamper == "nonnumeric-boundary":
        play["opponent_config"]["initial_ply"] = "bad"
    elif tamper == "oversized-boundary":
        play["opponent_config"]["initial_ply"] = "9" * 4300
    elif tamper == "illegal-source-prefix":
        game["moves"][0] = "0000"
    elif tamper == "excess-session-version":
        clock["version"] = 2
    restored = client.post("/v1/imports", headers=auth, json=archive)
    assert restored.status_code == (200 if tamper == "none" else 400), restored.text
    db_session.expire_all()
    if tamper == "none":
        assert db_session.get(PlaySession, session_id).opponent_config["initial_ply"] == "2"
        assert db_session.get(Game, game_id).moves == ["e2e4", "e7e5"]
        assert db_session.get(PlayClock, session_id).black_remaining_ms == 60_000
        current = client.get(f"/v1/play-sessions/{session_id}", headers=auth)
        assert current.status_code == 200, current.text
        assert current.json()["clock"]["version"] == 0
    else:
        assert db_session.get(Player, "alice") is None
        assert db_session.get(PlaySession, session_id) is None
        assert db_session.get(Game, game_id) is None
        assert db_session.get(PlayClock, session_id) is None


@pytest.mark.parametrize("source_plies", [2, 3])
def test_continuation_archive_retains_increments_earned_after_either_color_boundary(
    client: TestClient,
    db_session: Session,
    frozen_time: FrozenTime,
    monkeypatch: pytest.MonkeyPatch,
    source_plies: int,
) -> None:
    auth = register(db_session)
    source_moves = ["e2e4", "e7e5", "g1f3"][:source_plies]
    session_id, game_id = _continuation(client, db_session, auth, source_moves)
    human_move, reply = ("g1f3", "b8c6") if source_plies == 2 else ("b8c6", "f1b5")
    monkeypatch.setitem(
        app.dependency_overrides, play_api.get_opponent_provider, lambda: ScriptedOpponent(reply)
    )
    moved = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        headers=auth,
        json={"move": human_move, "expected_clock_version": 0},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["clock"]["version"] == 2
    archive = _export_then_delete(client, auth)
    saved = archive["play_clocks"][0]
    assert saved["white_remaining_ms"] == saved["black_remaining_ms"] == 62_000
    restored = client.post("/v1/imports", headers=auth, json=archive)
    assert restored.status_code == 200, restored.text
    db_session.expire_all()
    assert db_session.get(Game, game_id).moves == [*source_moves, human_move, reply]
    assert db_session.get(PlayClock, session_id).model_dump(mode="json") == saved


@pytest.mark.asyncio
async def test_clock_change_wait_cannot_return_active_snapshot_after_deadline_passes(
    db_session: Session, frozen_time: FrozenTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time)
    session_id = play.id
    db_session.rollback()
    engine = db_session.get_bind()
    runtime = PlayClockRuntime(engine)
    monkeypatch.setattr(app.state, "play_clock_runtime", runtime)
    monkeypatch.setattr(play_api, "CLOCK_CHANGE_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(play_api, "CLOCK_EXPIRY_WAIT_SECONDS", 1.0)
    original_wait = runtime.wait_for_change
    waits = 0

    async def advance_at_wait(observed: asyncio.Event) -> bool:
        nonlocal waits
        waits += 1
        if waits == 1:
            frozen_time.advance(60_000)
        return await original_wait(observed)

    monkeypatch.setattr(runtime, "wait_for_change", advance_at_wait)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await asyncio.wait_for(
                http.get(
                    f"/v1/play-sessions/{session_id}",
                    headers=auth,
                    params={"after_clock_version": 0},
                ),
                timeout=2,
            )
        assert response.status_code == 503, response.text
        assert "expiration is pending" in response.json()["detail"]
        assert response.headers["Retry-After"] == "1"
        assert waits == 2
        with Session(engine) as reader:
            assert reader.get(PlaySession, session_id).status == "active"
            assert reader.get(PlayClock, session_id).version == 0
    finally:
        await runtime.stop()
