from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import chess
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine, select

from scan64.api.models import DeletionAudit, Player
from scan64.content.models import ContentAttempt, ContentItem, StudySession
from scan64.content.puzzle_mode_models import PuzzleModeBinding
from scan64.content.puzzle_practice_models import (
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
)
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import _migration_config
from tests.integration.test_puzzle_lifecycle import _archive, puzzle_item, seed_puzzle_run
from tests.integration.test_puzzle_practice_archive import _practice_archive

PLAYER_ID = "mode-archive-owner"


def _seed_modes(
    client: TestClient,
    session: Session,
    *,
    player_id: str = PLAYER_ID,
) -> tuple[dict[str, Any], dict[str, str]]:
    registered = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": "Mode owner"},
    )
    assert registered.status_code == 200
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    now = datetime(2026, 11, 13, 12, tzinfo=UTC)
    session.add(PuzzleRating(player_id=player_id))
    session.add(PuzzleDailySet(player_id=player_id, utc_day="2026-11-13", created_at=now))
    for index, mode in enumerate(("theme", "streak")):
        item = puzzle_item()
        puzzle_id = f"{player_id}-{mode}"
        item.id = f"lichess:{puzzle_id}"
        item.payload = deepcopy(item.payload)
        item.payload.update(puzzle_id=puzzle_id, themes=["fork"])
        item.payload["lesson"]["lesson_id"] = item.id
        study = StudySession(player_id=player_id, domain="puzzles")
        session.add_all([item, study])
        session.flush()
        board = chess.Board(item.payload["lesson"]["source"]["fen"])
        board.push_uci("e7e5")
        started = now + timedelta(seconds=index * 2)
        completed = started + timedelta(seconds=1)
        run = PuzzleRun(
            player_id=player_id,
            item_id=item.id,
            session_id=study.id,
            state="solved",
            fen=board.fen(),
            cursor=1,
            played_moves=["e7e5"],
            revision=3,
            wrong_attempts=2,
            started_at=started,
            completed_at=completed,
        )
        session.add(run)
        session.flush()
        session.add(
            PuzzleModeBinding(
                run_id=run.id,
                player_id=player_id,
                mode=mode,
                theme="fork" if mode == "theme" else None,
                widening="400",
            )
        )
        session.add(
            ContentAttempt(
                id=run.id,
                item_id=item.id,
                player_id=player_id,
                session_id=study.id,
                success=True,
                started_at=started,
                completed_at=completed,
                response_payload={"run_id": run.id},
            )
        )
    session.commit()
    exported = client.post("/v1/exports", json={"player_id": player_id}, headers=headers)
    assert exported.status_code == 200
    return exported.json(), headers


def _delete(client: TestClient, player_id: str, headers: dict[str, str]) -> dict[str, Any]:
    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{player_id}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("fresh_catalog", [False, True])
def test_mode_archive_roundtrip_preserves_snapshots_and_unrated_history(
    client: TestClient,
    db_session: Session,
    fresh_catalog: bool,
) -> None:
    archive, headers = _seed_modes(client, db_session)
    assert {row["mode"] for row in archive["puzzle_mode_bindings"]} == {"theme", "streak"}
    assert archive["puzzle_daily_slots"] == archive["puzzle_rating_events"] == []
    dry_run = client.request(
        "DELETE",
        f"/v1/players/{PLAYER_ID}/data",
        headers=headers,
        json={"dry_run": True},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["affected_rows"]["puzzle_mode_bindings"] == 2
    assert len(db_session.exec(select(PuzzleModeBinding)).all()) == 2
    deletion = _delete(client, PLAYER_ID, headers)
    assert deletion["affected_rows"] == dry_run.json()["affected_rows"]
    db_session.expire_all()
    assert db_session.get(DeletionAudit, deletion["audit_id"]) is not None
    assert db_session.exec(select(PuzzleModeBinding)).all() == []
    for data in archive["puzzle_content_items"]:
        item = db_session.get(ContentItem, data["id"])
        assert item is not None
        if fresh_catalog:
            db_session.delete(item)
        else:
            payload = deepcopy(item.payload)
            payload["themes"] = ["pin"]
            item.payload = payload
            db_session.add(item)
        # Even archived catalog metadata is not authoritative for the historical snapshot.
        data["payload"]["themes"] = ["skewer"]
    db_session.commit()
    restored = client.post("/v1/imports", json=archive, headers=headers)
    assert restored.status_code == 200, restored.text
    db_session.expire_all()
    for data in archive["puzzle_mode_bindings"]:
        binding = db_session.get(PuzzleModeBinding, data["run_id"])
        assert binding is not None
        assert binding.model_dump(mode="json") == data
    for data in archive["puzzle_content_items"]:
        item = db_session.get(ContentItem, data["id"])
        assert item is not None
        assert item.payload["themes"] == (["skewer"] if fresh_catalog else ["pin"])
    assert db_session.exec(select(PuzzleDailySlot)).all() == []
    assert db_session.exec(select(PuzzleRatingEvent)).all() == []
    assert db_session.exec(select(ReviewSchedule)).all() == []
    rating = db_session.get(PuzzleRating, PLAYER_ID)
    assert rating is not None and rating.rating == 1200 and rating.rated_count == 0


@pytest.mark.parametrize("daily_history", [False, True])
@pytest.mark.parametrize("fresh_catalog", [False, True])
def test_archives_before_mode_table_restore_without_inventing_bindings(
    client: TestClient,
    db_session: Session,
    daily_history: bool,
    fresh_catalog: bool,
) -> None:
    archive, headers = (
        _practice_archive(client, db_session) if daily_history else _archive(client, db_session)
    )
    archive.pop("puzzle_mode_bindings")
    if fresh_catalog:
        for data in archive["puzzle_content_items"]:
            item = db_session.get(ContentItem, data["id"])
            assert item is not None
            db_session.delete(item)
        db_session.commit()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.exec(select(PuzzleModeBinding)).all() == []
    assert len(db_session.exec(select(PuzzleRun)).all()) == len(archive["puzzle_runs"])
    assert len(db_session.exec(select(PuzzleRatingEvent)).all()) == len(
        archive["puzzle_rating_events"]
    )


@pytest.mark.parametrize("state", ["hinted", "failed", "abandoned", "active"])
def test_mode_restore_preserves_hint_and_terminal_history(
    client: TestClient,
    db_session: Session,
    state: str,
) -> None:
    archive, headers = _seed_modes(client, db_session)
    _delete(client, PLAYER_ID, headers)
    binding = next(row for row in archive["puzzle_mode_bindings"] if row["mode"] == "streak")
    run = next(row for row in archive["puzzle_runs"] if row["id"] == binding["run_id"])
    attempt = next(row for row in archive["content_attempts"] if row["id"] == run["id"])
    if state == "hinted":
        run["hint_steps"] = {"0": 1}
        run["revision"] = 4
        attempt["hint_assisted"] = True
    else:
        item = next(row for row in archive["puzzle_content_items"] if row["id"] == run["item_id"])
        run.update(
            state=state,
            cursor=0,
            played_moves=[],
            fen=item["payload"]["lesson"]["source"]["fen"],
            wrong_attempts=3 if state == "failed" else 2,
        )
        if state == "failed":
            attempt["success"] = False
        else:
            archive["content_attempts"].remove(attempt)
        if state == "active":
            run["completed_at"] = None
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    restored = db_session.get(PuzzleRun, run["id"])
    assert restored is not None and restored.model_dump(mode="json") == run
    restored_binding = db_session.get(PuzzleModeBinding, binding["run_id"])
    assert restored_binding is not None and restored_binding.model_dump(mode="json") == binding
    assert db_session.exec(select(PuzzleRatingEvent)).all() == []
    assert db_session.exec(select(ReviewSchedule)).all() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "missing-run"),
        ("player_id", "foreign-player"),
        ("mode", "daily"),
        ("mode", "mistakes"),
        ("mode", ""),
        ("mode", None),
        ("theme", None),
        ("theme", ""),
        ("theme", "  "),
        ("theme", 3),
        ("widening", "due"),
        ("widening", "800"),
        ("widening", None),
    ],
)
def test_restore_rejects_invalid_mode_bindings_atomically(
    client: TestClient,
    db_session: Session,
    field: str,
    value: Any,
) -> None:
    archive, headers = _seed_modes(client, db_session)
    _delete(client, PLAYER_ID, headers)
    row = next(binding for binding in archive["puzzle_mode_bindings"] if binding["mode"] == "theme")
    row[field] = value
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    db_session.expire_all()
    assert db_session.get(Player, PLAYER_ID) is None
    assert db_session.exec(select(PuzzleModeBinding)).all() == []
    assert db_session.exec(select(PuzzleRun)).all() == []


@pytest.mark.parametrize("invalid_kind", ["streak-theme", "duplicate", "foreign-run"])
def test_restore_rejects_inconsistent_mode_graph(
    client: TestClient,
    db_session: Session,
    invalid_kind: str,
) -> None:
    archive, headers = _seed_modes(client, db_session)
    _delete(client, PLAYER_ID, headers)
    if invalid_kind == "streak-theme":
        next(row for row in archive["puzzle_mode_bindings"] if row["mode"] == "streak")["theme"] = (
            "fork"
        )
    elif invalid_kind == "duplicate":
        archive["puzzle_mode_bindings"].append(deepcopy(archive["puzzle_mode_bindings"][0]))
    else:
        archive["puzzle_runs"][0]["player_id"] = "foreign-player"
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    db_session.expire_all()
    assert db_session.get(Player, PLAYER_ID) is None
    assert db_session.exec(select(PuzzleModeBinding)).all() == []


@pytest.mark.parametrize("mode", ["theme", "streak"])
def test_restore_rejects_mode_binding_on_daily_slot(
    client: TestClient,
    db_session: Session,
    mode: str,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    run = archive["puzzle_runs"][0]
    archive["puzzle_mode_bindings"] = [
        {
            "run_id": run["id"],
            "player_id": run["player_id"],
            "mode": mode,
            "theme": "fork" if mode == "theme" else None,
            "widening": "200",
        }
    ]
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    db_session.expire_all()
    assert db_session.get(Player, run["player_id"]) is None
    assert db_session.exec(select(PuzzleModeBinding)).all() == []


def test_mode_export_and_deletion_are_player_scoped(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _seed_modes(client, db_session)
    other_archive, other_headers = _seed_modes(client, db_session, player_id="other-mode-owner")
    assert len(archive["puzzle_mode_bindings"]) == len(other_archive["puzzle_mode_bindings"]) == 2
    forbidden = client.request(
        "DELETE",
        f"/v1/players/{PLAYER_ID}/data",
        headers=other_headers,
        json={"dry_run": True},
    )
    assert forbidden.status_code == 403
    _delete(client, PLAYER_ID, headers)
    db_session.expire_all()
    for data in archive["puzzle_mode_bindings"]:
        assert db_session.get(PuzzleModeBinding, data["run_id"]) is None
        assert db_session.get(PuzzleRun, data["run_id"]) is None
    for data in other_archive["puzzle_mode_bindings"]:
        assert db_session.get(PuzzleModeBinding, data["run_id"]) is not None
    for data in archive["puzzle_content_items"] + other_archive["puzzle_content_items"]:
        assert db_session.get(ContentItem, data["id"]) is not None


def test_mode_migration_preserves_populated_database_across_upgrade_and_downgrade(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'mode-copy.db'}")
    config = _migration_config(engine)
    command.upgrade(config, "20261113_14")
    with Session(engine) as session:
        session.add(Player(id="migration-mode-owner", preferences={"theme": "dark"}))
        session.commit()
        run = seed_puzzle_run(session, "migration-mode-owner", solved=True)
        run_id = run.id
        before = run.model_dump(mode="json")
    command.upgrade(config, "20261113_15")
    schema = inspect(engine)
    assert schema.get_pk_constraint("puzzlemodebinding")["constrained_columns"] == ["run_id"]
    assert {fk["referred_table"] for fk in schema.get_foreign_keys("puzzlemodebinding")} == {
        "player",
        "puzzlerun",
    }
    assert any(
        index["column_names"] == ["player_id"] for index in schema.get_indexes("puzzlemodebinding")
    )
    with Session(engine) as session:
        assert session.exec(select(PuzzleModeBinding)).all() == []
        run = session.get(PuzzleRun, run_id)
        assert run is not None and run.model_dump(mode="json") == before
        session.add(
            PuzzleModeBinding(
                run_id=run_id,
                player_id="migration-mode-owner",
                mode="theme",
                theme="fork",
                widening="catalog",
            )
        )
        session.commit()
    command.downgrade(config, "20261113_14")
    assert "puzzlemodebinding" not in inspect(engine).get_table_names()
    with Session(engine) as session:
        run = session.get(PuzzleRun, run_id)
        assert run is not None and run.model_dump(mode="json") == before
        player = session.get(Player, "migration-mode-owner")
        assert player is not None and player.preferences == {"theme": "dark"}
        assert len(session.exec(select(ContentAttempt)).all()) == 1
        assert len(session.exec(select(ContentItem)).all()) == 1
    command.upgrade(config, "20261113_15")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM puzzlemodebinding")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM puzzlerun")).scalar_one() == 1
    engine.dispose()


def test_mode_deletion_can_roll_back_without_losing_bindings_or_history(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, headers = _seed_modes(client, db_session)

    def abort_commit() -> None:
        raise RuntimeError("injected deletion commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", abort_commit)
        with pytest.raises(RuntimeError, match="injected deletion commit failure"):
            client.request(
                "DELETE",
                f"/v1/players/{PLAYER_ID}/data",
                headers=headers,
                json={"dry_run": False, "confirmation": f"delete-{PLAYER_ID}"},
            )
    db_session.rollback()
    assert db_session.get(Player, PLAYER_ID) is not None
    for data in archive["puzzle_mode_bindings"]:
        binding = db_session.get(PuzzleModeBinding, data["run_id"])
        assert binding is not None and binding.model_dump(mode="json") == data
        assert db_session.get(PuzzleRun, data["run_id"]) is not None
    assert len(db_session.exec(select(ContentAttempt)).all()) == 2
    assert db_session.exec(select(DeletionAudit)).all() == []
