"""The first in-app import must be playable without waiting for UTC midnight."""

from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest
import zstandard
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select

import scan64.content.puzzle_practice as practice
from scan64.content.puzzle_practice_models import PuzzleDailySet, PuzzleDailySlot
from scan64.desktop_import import run_import
from tests.integration.test_puzzle_runs import _auth

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def import_fixture(session: Session, tmp_path: Path, puzzle_id: str) -> None:
    csv = (
        "PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes\n"
        f"{puzzle_id},rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1,"
        "e2e4 e7e5,1200,80,90,200,opening\n"
    )
    path = tmp_path / f"{puzzle_id}.csv.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress(csv.encode()))
    engine = session.get_bind()
    assert isinstance(engine, Engine)
    summary = run_import(engine, path, Event(), lambda _: None)
    assert summary.inserted == 1
    session.expire_all()


@pytest.mark.parametrize("route", ["/active", "/daily", "/runs"])
def test_empty_catalog_does_not_freeze_day_before_first_import(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    monkeypatch.setattr(practice, "utc_now", lambda: NOW)
    auth = _auth(client)
    response = client.request(
        "POST" if route == "/runs" else "GET", f"/v1/puzzles{route}", headers=auth
    )
    assert response.status_code == 200
    assert not db_session.exec(select(PuzzleDailySet)).all()
    assert not db_session.exec(select(PuzzleDailySlot)).all()
    import_fixture(db_session, tmp_path, "firstimport")
    response = client.post("/v1/puzzles/runs", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    assert response.json()["run"]["puzzle_id"] == "lichess:firstimport"
    assert response.json()["daily"]["utc_day"] == NOW.date().isoformat()
    assert len(db_session.exec(select(PuzzleDailySet)).all()) == 1


def test_underfilled_nonempty_day_is_unchanged_after_later_import(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(practice, "utc_now", lambda: NOW)
    auth = _auth(client)
    import_fixture(db_session, tmp_path, "original")
    before = client.get("/v1/puzzles/daily", headers=auth).json()
    day = db_session.exec(select(PuzzleDailySet)).one().model_dump()
    slots = [slot.model_dump() for slot in db_session.exec(select(PuzzleDailySlot)).all()]
    assert before["total"] == 1
    import_fixture(db_session, tmp_path, "later")
    after = client.get("/v1/puzzles/daily", headers=auth).json()
    assert after == before
    assert db_session.exec(select(PuzzleDailySet)).one().model_dump() == day
    assert [slot.model_dump() for slot in db_session.exec(select(PuzzleDailySlot)).all()] == slots
