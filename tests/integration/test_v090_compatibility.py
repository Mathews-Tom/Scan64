"""Real v0.9.0 progress, not a head-built approximation of the old schema."""

from __future__ import annotations

import gzip
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.persistence.database as database
from scan64.api.app import app
from scan64.api.models import PlayerCredential, issue_player_token
from scan64.desktop_backups import VERSION_MARKER, prepare_desktop_backup
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule

FIXTURES = Path(__file__).parents[1] / "fixtures" / "v0_9_0"


def _subset(actual: Any, expected: Any) -> None:
    """New response fields are allowed; legacy visible values may not silently change."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        for key, value in expected.items():
            assert key in actual, key
            _subset(actual[key], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            _subset(actual_item, expected_item)
    else:
        assert actual == expected


def _get(client: TestClient, path: str) -> Any:
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def _utc(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


@pytest.mark.parametrize("path", ["database_upgrade", "archive_import"])
def test_v090_progress_survives_at_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    expected = json.loads((FIXTURES / "expected.json").read_text())
    archive = json.loads(gzip.decompress((FIXTURES / "archive.json.gz").read_bytes()))
    player = expected["player"]
    database_path = tmp_path / "scan64.db"
    if path == "database_upgrade":
        original = gzip.decompress((FIXTURES / "scan64.db.gz").read_bytes())
        database_path.write_bytes(original)
        (tmp_path / VERSION_MARKER).write_text("0.9.0\n")
        backup = prepare_desktop_backup(tmp_path, "1.0.0")
        (snapshot,) = (tmp_path / "backups").glob("scan64-*-before-*.db")
        with sqlite3.connect(snapshot) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20261117_18",
            )
            assert connection.execute("SELECT count(*) FROM game").fetchone() == (3,)
    engine = database.create_database_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(database, "engine", engine)
    try:
        database.migrate_database(engine)
        if path == "database_upgrade":
            backup.record_successful_startup()
        token, token_hash = issue_player_token()
        with Session(engine) as session:
            if path == "database_upgrade":
                credential = session.get(PlayerCredential, player)
                assert credential is not None
                credential.token_hash = token_hash
                session.add(credential)
                session.commit()
            else:
                # Raw bearer tokens are deliberately not stored in public fixtures.
                archive["credential_hash"] = token_hash

            def get_session() -> Iterator[Session]:
                yield session

            app.dependency_overrides[database.get_session] = get_session
            try:
                # No lifespan: migrations above are the operation under test; no engine
                # workers, catalogue seeding or background schedulers are needed to read.
                client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
                if path == "archive_import":
                    response = client.post("/v1/imports", json=archive)
                    assert response.status_code == 200, response.text
                _subset(_get(client, f"/v1/players/{player}/profile"), expected["profile"])
                progress = _get(client, f"/v1/players/{player}/progress")
                assert progress["skills"]
                progress["skills"].sort(key=lambda item: item["concept"])
                expected["progress"]["skills"].sort(key=lambda item: item["concept"])
                _subset(progress, expected["progress"])
                games = _get(client, f"/v1/players/{player}/games")
                assert len(games["items"]) == len(expected["games"]) == 3
                for game in expected["games"]:
                    game_id = game["id"]
                    assert _get(client, f"/v1/games/{game_id}")["pgn"] == game["pgn"]
                    _subset(_get(client, f"/v1/games/{game_id}/analysis-status"), game["analysis"])
                    _subset(_get(client, f"/v1/games/{game_id}/review"), game["review"])
                for run in expected["puzzle_runs"]:
                    actual_run = _get(client, f"/v1/puzzles/runs/{run['run_id']}")
                    # The live daily set rolls over; the historical run does not.
                    for key in (
                        "run_id",
                        "puzzle_id",
                        "played_moves",
                        "cursor",
                        "state",
                        "wrong_attempts",
                        "hint_step",
                        "rated",
                        "mode",
                        "theme",
                    ):
                        assert actual_run[key] == run[key]
                    assert actual_run["state"] == "solved"
                    for key in ("rating", "rated_count", "provisional"):
                        assert actual_run["daily"][key] == run["daily"][key]
                response = client.post("/v1/exports", json={"player_id": player})
                assert response.status_code == 200, response.text
                restored = response.json()
                _subset(restored["puzzle_rating"], archive["puzzle_rating"])
                _subset(restored["content_attempts"], archive["content_attempts"])
                assert expected["lesson_attempts"]
                assert all(attempt["success"] for attempt in expected["lesson_attempts"])
                _subset(restored["lesson_attempts"], expected["lesson_attempts"])
                assert expected["skill_states"]
                _subset(restored["skill_states"], expected["skill_states"])
                schedules = session.exec(
                    select(ReviewSchedule).where(ReviewSchedule.player_id == player)
                ).all()
                assert len(schedules) == len(expected["review_schedules"]) > 0
                by_item = {schedule.item_id: schedule for schedule in schedules}
                for old in expected["review_schedules"]:
                    schedule = by_item[old["item_id"]]
                    assert _utc(schedule.next_review_at) == _utc(old["next_review_at"])
                    assert schedule.skill_id == old["skill_id"]
                    assert schedule.retired_at == old["retired_at"]
                    if old["last_reviewed_at"] is None:
                        assert schedule.last_reviewed_at is None
                    else:
                        assert schedule.last_reviewed_at is not None
                        assert _utc(schedule.last_reviewed_at) == _utc(old["last_reviewed_at"])
                    # M96 preserves old due dates as ungraded FSRS learning cards.
                    assert schedule.fsrs_state == 1
                    assert schedule.fsrs_stability is None
            finally:
                app.dependency_overrides.pop(database.get_session, None)
                client.close()
    finally:
        engine.dispose()
