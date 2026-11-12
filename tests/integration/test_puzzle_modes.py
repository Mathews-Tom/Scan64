from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from itertools import count
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from scan64.api.app import app
from scan64.content.models import ContentAttempt, ContentItem
from scan64.content.puzzle_mode_models import PuzzleModeBinding
from scan64.content.puzzle_practice_models import PuzzleDailySet, PuzzleDailySlot, PuzzleRatingEvent
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import get_session
from tests.integration.test_puzzle_practice import NOW, clock, seed
from tests.integration.test_puzzle_runs import _action, _auth, _safe, _start


def start(client: TestClient, auth: dict[str, str], mode: str = "streak", **fields: Any) -> dict:
    response = client.post("/v1/puzzles/runs", headers=auth, json={"mode": mode, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def reserve(client: TestClient, session: Session, auth: dict[str, str]) -> dict:
    seed(session, "daily-reserved")
    response = client.get("/v1/puzzles/daily", headers=auth)
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("mode", ["theme", "streak"])
@pytest.mark.parametrize("outcome", ["solve", "wrong_solve", "hint", "fail", "abandon"])
def test_modes_isolate_daily_rating_and_reviews(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    outcome: str,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    daily = reserve(client, db_session, auth)
    seed(db_session, "mode", themes=["fork"])
    result = start(client, auth, mode, **({"theme": "fork"} if mode == "theme" else {}))
    run = result["run"]
    _safe(run)
    assert run["mode"] == mode and not run["rated"]
    assert run["puzzle_id"] == "lichess:mode"
    assert run["daily"] == daily
    resumed = start(client, auth, "daily")["run"]
    assert resumed == run
    if outcome == "hint":
        run = _action(client, auth, run, "hints", step=1).json()
    if outcome == "wrong_solve":
        run = _action(client, auth, run, "moves", move="a7a6").json()
    if outcome == "fail":
        for _ in range(3):
            run = _action(client, auth, run, "moves", move="a7a6").json()
    elif outcome == "abandon":
        run = _action(client, auth, run, "abandon").json()
    else:
        run = _action(client, auth, run, "moves", move="e7e5").json()
    assert run["state"] != "active"
    assert run["daily"] == daily
    assert not db_session.exec(select(PuzzleRatingEvent)).all()
    assert not db_session.exec(select(ReviewSchedule)).all()
    db_session.expire_all()
    slot = db_session.exec(select(PuzzleDailySlot)).one()
    assert slot.run_id is None and slot.closed_at is None
    assert bool(db_session.exec(select(ContentAttempt)).all()) == (outcome != "abandon")
    expected = int(mode == "streak" and outcome in {"solve", "wrong_solve"})
    assert run["streak"]["current"] == expected
    before = run["streak"]
    assert _action(client, auth, run, "abandon").status_code == 409
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json()["streak"] == before
    # Exhaustion cannot reroll Daily, and returning to Daily still starts its original slot.
    assert (
        start(client, auth, mode, **({"theme": "fork"} if mode == "theme" else {}))["status"]
        == "exhausted"
    )
    assert _start(client, auth)["puzzle_id"] == "lichess:daily-reserved"


def test_nearest_widening_theme_and_exhaustion(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    reserve(client, db_session, auth)
    for name, rating, sequence, themes in [
        ("near-second", 1250, 2, ["fork"]),
        ("near-first", 1150, 1, ["fork"]),
        ("wide", 1500, 3, ["fork"]),
        ("catalog", 2000, 4, ["fork"]),
        ("other", 1200, 0, ["pin"]),
    ]:
        seed(db_session, name, rating, themes, sequence)
    modes = client.get("/v1/puzzles/modes", headers=auth).json()
    assert modes["themes"] == ["fork", "pin"]
    for name, widening in [
        ("near-first", "200"),
        ("near-second", "200"),
        ("wide", "400"),
        ("catalog", "catalog"),
    ]:
        run = start(client, auth, "theme", theme="fork")["run"]
        assert run["puzzle_id"] == f"lichess:{name}" and run["widening"] == widening
        _action(client, auth, run, "abandon")
    assert start(client, auth, "theme", theme="fork")["status"] == "exhausted"
    assert start(client, auth)["run"]["puzzle_id"] == "lichess:other"
    assert client.post("/v1/puzzles/runs", headers=auth, json={"mode": "theme"}).status_code == 422
    assert (
        client.post(
            "/v1/puzzles/runs", headers=auth, json={"mode": "streak", "theme": "fork"}
        ).status_code
        == 422
    )


def test_streak_only_derivation_hint_reset_and_no_repeat(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = count()
    monkeypatch.setattr(
        "scan64.content.puzzle_practice.utc_now",
        lambda: NOW + timedelta(seconds=next(ticks)),
    )
    auth = _auth(client)
    reserve(client, db_session, auth)
    for index in range(6):
        seed(db_session, f"extra-{index}", themes=["fork"], sequence=index)
    used = set()
    for index in range(2):
        run = start(client, auth)["run"]
        used.add(run["puzzle_id"])
        run = _action(client, auth, run, "moves", move="a7a6").json()
        run = _action(client, auth, run, "moves", move="e7e5").json()
        assert run["streak"]["current"] == index + 1
    theme = start(client, auth, "theme", theme="fork")["run"]
    used.add(theme["puzzle_id"])
    theme = _action(client, auth, theme, "abandon").json()
    assert theme["streak"]["current"] == 2
    daily = _start(client, auth)
    daily = _action(client, auth, daily, "abandon").json()
    assert daily["streak"]["current"] == 2
    run = start(client, auth)["run"]
    assert run["puzzle_id"] not in used
    run = _action(client, auth, run, "hints", step=1).json()
    # A hint resets only when the run finishes, not while it remains resumable.
    assert run["streak"]["current"] == 2
    run = _action(client, auth, run, "moves", move="e7e5").json()
    assert run["streak"] == {"current": 0, "best": 2, "reset_reason": "hint"}
    next_run = start(client, auth)["run"]
    next_run = _action(client, auth, next_run, "moves", move="e7e5").json()
    assert next_run["streak"]["current"] == 1 and next_run["streak"]["best"] == 2
    last = start(client, auth)["run"]
    last = _action(client, auth, last, "abandon").json()
    assert last["streak"] == {"current": 0, "best": 2, "reset_reason": "abandoned"}
    assert start(client, auth)["status"] == "exhausted"


def test_mode_start_creates_daily_first_and_theme_metadata_only(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    for index in range(12):
        seed(db_session, f"item-{index}", themes=["fork"], sequence=index)
    statements: list[str] = []
    bind = db_session.get_bind()

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", observe)
    try:
        assert client.get("/v1/puzzles/modes", headers=auth).json()["themes"] == ["fork"]
    finally:
        event.remove(bind, "before_cursor_execute", observe)
    content_reads = [statement for statement in statements if "FROM contentitem" in statement]
    assert len(content_reads) == 1
    assert "JSON_QUOTE(JSON_EXTRACT(contentitem.payload" in content_reads[0]
    assert "contentitem.id" not in content_reads[0]
    run = start(client, auth)["run"]
    slots = db_session.exec(select(PuzzleDailySlot)).all()
    assert len(slots) == 10
    assert run["puzzle_id"] not in {slot.item_id for slot in slots}
    assert len(db_session.exec(select(PuzzleDailySet)).all()) == 1


@pytest.mark.parametrize("other", ["daily", "streak"])
def test_daily_mode_reservation_and_start_race(
    client: TestClient,
    tmp_path: Path,
    other: str,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mode-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    previous = app.dependency_overrides[get_session]

    def sessions():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    try:
        auth = _auth(client)
        with Session(engine) as session:
            for index in range(12):
                seed(session, f"item-{index}", sequence=index)
        barrier = Barrier(2)

        def request(mode: str):
            barrier.wait(timeout=10)
            if mode == "daily":
                return client.get("/v1/puzzles/daily", headers=auth)
            return client.post("/v1/puzzles/runs", headers=auth, json={"mode": mode})

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(request, ["streak", other]))
        assert [response.status_code for response in responses] == [200, 200]
        with Session(engine) as session:
            run = session.exec(select(PuzzleRun)).one()
            assert session.get(PuzzleModeBinding, run.id).mode == "streak"
            assert run.item_id not in {
                slot.item_id for slot in session.exec(select(PuzzleDailySlot)).all()
            }
            assert len(session.exec(select(PuzzleDailySet)).all()) == 1
    finally:
        app.dependency_overrides[get_session] = previous
        engine.dispose()


def test_theme_snapshot_survives_metadata_change_and_owner_auth(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    reserve(client, db_session, auth)
    seed(db_session, "snapshot", themes=["fork"])
    run = start(client, auth, "theme", theme="fork")["run"]
    item = db_session.get(ContentItem, run["puzzle_id"])
    item.payload = {**item.payload, "themes": ["pin"]}
    db_session.add(item)
    db_session.commit()
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json()["theme"] == "fork"
    other = _auth(client, "other-player")
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=other).status_code == 404
    assert client.post("/v1/puzzles/runs", json={"mode": "streak"}).status_code == 401


def test_resume_only_never_starts_daily_and_preserves_finished_streak_choice(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    for index in range(12):
        seed(db_session, f"resume-{index}", themes=["fork"], sequence=index)
    for _ in range(2):
        response = client.get("/v1/puzzles/active", headers=auth)
        assert response.status_code == 200
        assert response.json()["status"] == "idle" and response.json()["run"] is None
        assert response.json()["daily"]["remaining"] == 10
    assert not db_session.exec(select(PuzzleRun)).all()
    assert not db_session.exec(select(ContentAttempt)).all()
    assert not db_session.exec(select(PuzzleRatingEvent)).all()
    run = start(client, auth)["run"]
    assert client.get("/v1/puzzles/active", headers=auth).json()["run"] == run
    run = _action(client, auth, run, "moves", move="e7e5").json()
    resumed = client.get("/v1/puzzles/active", headers=auth).json()
    assert resumed["status"] == "idle" and resumed["run"] is None
    assert resumed["streak"]["current"] == 1
    assert len(db_session.exec(select(PuzzleRun)).all()) == 1
    assert not db_session.exec(select(PuzzleRatingEvent)).all()


def test_mode_reserves_day_after_waiting_for_player_lock(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scan64.content.puzzle_practice as practice

    clock(monkeypatch, NOW.replace(hour=23, minute=59))
    auth = _auth(client)
    for index in range(12):
        seed(db_session, f"midnight-{index}", sequence=index)
    original = practice.lock_puzzle_player

    def after_midnight(player_id: str, session: Session) -> None:
        clock(monkeypatch, NOW + timedelta(days=1))
        original(player_id, session)

    monkeypatch.setattr(practice, "lock_puzzle_player", after_midnight)
    run = start(client, auth)["run"]
    day = db_session.exec(select(PuzzleDailySet)).one()
    assert day.utc_day == "2026-11-14"
    assert run["puzzle_id"] not in {
        slot.item_id for slot in db_session.exec(select(PuzzleDailySlot)).all()
    }
