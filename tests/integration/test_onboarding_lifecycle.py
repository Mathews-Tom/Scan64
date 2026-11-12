from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import chess
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine, select

from scan64.api.models import DeletionAudit, Player, PlayerOnboarding, PlayerProfile
from scan64.content.models import ContentAttempt, ContentItem, StudySession
from scan64.content.puzzle_practice_models import (
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
    elo_after,
)
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import _migration_config
from tests.integration.test_puzzle_lifecycle import puzzle_item

PLAYER_ID = "onboarding-lifecycle-owner"


def _seed_history(session: Session, player_id: str, *, active_index: int | None = None) -> datetime:
    """Include pre-opt-in and non-daily history, six intro runs and an unstarted slot."""
    base = (datetime.now(UTC) - timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    clock = base + timedelta(minutes=1)
    daily = PuzzleDailySet(player_id=player_id, utc_day=base.date().isoformat(), created_at=base)
    session.add(daily)
    session.flush()
    before = 1200.0
    sequence = 0
    position = 0
    outcomes = ["solved", "solved", "solved", "hinted", "failed", "abandoned", "solved", "solved"]
    for index, outcome in enumerate(outcomes):
        intro_index = index - 2
        if intro_index == active_index:
            outcome = "active"
        item = puzzle_item()
        item.id = f"lichess:onboarding-{index}"
        item.payload = deepcopy(item.payload)
        item.payload.update(puzzle_id=f"onboarding-{index}", import_sequence=index)
        item.payload["lesson"]["lesson_id"] = item.id
        study = StudySession(player_id=player_id, domain="puzzles")
        session.add_all([item, study])
        session.flush()
        started = base if index == 0 else clock + timedelta(seconds=index * 2)
        completed = None if outcome == "active" else started + timedelta(seconds=1)
        solved = outcome in {"solved", "hinted"}
        board = chess.Board(item.payload["lesson"]["source"]["fen"])
        if solved:
            board.push_uci("e7e5")
        run = PuzzleRun(
            id=f"onboarding-run-{index}",
            player_id=player_id,
            item_id=item.id,
            session_id=study.id,
            state="solved" if solved else outcome,
            fen=board.fen(),
            cursor=int(solved),
            played_moves=["e7e5"] if solved else [],
            wrong_attempts=3 if outcome == "failed" else 0,
            hint_steps={"0": 1} if outcome == "hinted" else {},
            revision=3
            if outcome == "failed"
            else 2
            if outcome == "hinted"
            else int(outcome != "active"),
            started_at=started,
            completed_at=completed,
        )
        session.add(run)
        session.flush()
        if outcome not in {"active", "abandoned"}:
            session.add(
                ContentAttempt(
                    id=run.id,
                    player_id=player_id,
                    item_id=item.id,
                    session_id=study.id,
                    success=solved,
                    hint_assisted=outcome == "hinted",
                    completed_at=completed,
                    response_payload={"run_id": run.id},
                )
            )
        if index == 1:
            continue  # Post-clock practice without a daily binding never counts as introductory.
        slot = PuzzleDailySlot(
            player_id=player_id,
            set_id=daily.id,
            position=position,
            item_id=item.id,
            item_rating=1200,
            reason="exploration",
            widening="200",
            first_exposure=True,
            run_id=run.id,
            closed_at=completed,
        )
        position += 1
        session.add(slot)
        session.flush()
        if completed is not None:
            sequence += 1
            score = int(outcome == "solved")
            after = elo_after(before, slot.item_rating, score)
            session.add(
                PuzzleRatingEvent(
                    run_id=run.id,
                    slot_id=slot.id,
                    player_id=player_id,
                    item_id=item.id,
                    sequence=sequence,
                    item_rating=slot.item_rating,
                    before=before,
                    after=after,
                    score=score,
                    created_at=completed,
                )
            )
            before = after
    unplayed = puzzle_item()
    unplayed.id = "lichess:onboarding-unstarted"
    unplayed.payload = deepcopy(unplayed.payload)
    unplayed.payload.update(puzzle_id="onboarding-unstarted", import_sequence=8)
    unplayed.payload["lesson"]["lesson_id"] = unplayed.id
    session.add(unplayed)
    session.flush()
    session.add(
        PuzzleDailySlot(
            player_id=player_id,
            set_id=daily.id,
            position=position,
            item_id=unplayed.id,
            item_rating=1200,
            reason="exploration",
            widening="200",
            first_exposure=True,
        )
    )
    session.add(PuzzleRating(player_id=player_id, rating=before, rated_count=sequence))
    session.add(
        SkillState(
            player_id=player_id,
            concept_code="tactics.hanging_piece",
            alpha=11.5,
            beta=7.25,
            prior_alpha=3,
            prior_beta=2,
            last_updated=base,
        )
    )
    session.add(ReviewSchedule(player_id=player_id, item_id="measured-lesson", next_review_at=base))
    session.commit()
    return clock


def _owner_archive(
    client: TestClient,
    session: Session,
    *,
    active_index: int | None = None,
    status: str = "in_progress",
    placed: bool = True,
) -> tuple[dict[str, Any], dict[str, str]]:
    registration = client.post(
        "/v1/players",
        json={"id": PLAYER_ID, "display_name": "Onboarding archive owner"},
    )
    assert registration.status_code == 200
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    clock = _seed_history(session, PLAYER_ID, active_index=active_index)
    row = session.get(PlayerOnboarding, PLAYER_ID)
    assert row is not None
    row.status = status
    if placed:
        row.declared_level = "developing"
        row.suggested_bot_level = 3
        row.mapping_version = "declared-level-v1"
        row.intro_started_at = clock
    if status == "skipped":
        row.skipped_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(row)
    session.commit()
    exported = client.post("/v1/exports", json={"player_id": PLAYER_ID}, headers=headers)
    assert exported.status_code == 200, exported.text
    session.commit()
    return exported.json(), headers


def _delete(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.request(
        "DELETE",
        f"/v1/players/{PLAYER_ID}/data",
        headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{PLAYER_ID}"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _reject(
    client: TestClient, session: Session, archive: dict[str, Any], headers: dict[str, str]
) -> None:
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert "malformed onboarding" in response.json()["detail"]
    session.expire_all()
    assert session.get(Player, PLAYER_ID) is None
    assert session.get(PlayerOnboarding, PLAYER_ID) is None
    assert session.exec(select(PuzzleRun)).all() == []
    assert session.exec(select(PuzzleRatingEvent)).all() == []
    assert session.exec(select(SkillState)).all() == []


@pytest.mark.parametrize("fresh_catalog", [False, True])
def test_onboarding_roundtrip_derives_first_five_without_replaying_measured_history(
    client: TestClient,
    db_session: Session,
    fresh_catalog: bool,
) -> None:
    archive, headers = _owner_archive(client, db_session)
    row = archive["onboarding"]
    assert row["status"] == "completed"  # Export normalizes a lazy, in-progress row.
    fifth = next(run for run in archive["puzzle_runs"] if run["id"] == "onboarding-run-6")
    assert row["completed_at"] == fifth["completed_at"]
    assert not {"intro_started", "intro_closed", "intro_target"}.intersection(row)
    _delete(client, headers)
    if fresh_catalog:
        for item in db_session.exec(select(ContentItem)).all():
            db_session.delete(item)
        db_session.commit()
    restored = client.post("/v1/imports", json=archive, headers=headers)
    assert restored.status_code == 200, restored.text
    exported = client.post("/v1/exports", json={"player_id": PLAYER_ID}, headers=headers)
    assert exported.status_code == 200
    for field in (
        "onboarding",
        "profile",
        "puzzle_rating",
        "puzzle_rating_events",
        "puzzle_daily_sets",
        "puzzle_daily_slots",
        "puzzle_runs",
        "content_attempts",
        "skill_states",
        "review_schedules",
    ):
        assert exported.json()[field] == archive[field], field
    progress = client.get(f"/v1/players/{PLAYER_ID}/onboarding", headers=headers)
    assert progress.status_code == 200
    assert progress.json()["intro_started"] == progress.json()["intro_closed"] == 5
    profile = db_session.get(PlayerProfile, PLAYER_ID)
    assert profile is not None and profile.rating == 1500


@pytest.mark.parametrize("onboarding_field", ["missing", "null"])
@pytest.mark.parametrize("fresh_catalog", [False, True])
def test_old_archives_restore_unplaced_without_trusting_preferences(
    client: TestClient,
    db_session: Session,
    onboarding_field: str,
    fresh_catalog: bool,
) -> None:
    archive, headers = _owner_archive(client, db_session)
    _delete(client, headers)
    if onboarding_field == "missing":
        archive.pop("onboarding")
    else:
        archive["onboarding"] = None
    archive["player"]["preferences"].update(
        {
            "onboarding": {
                "status": "completed",
                "declared_level": "advanced",
                "suggested_bot_level": 6,
            },
            "declared_level": "advanced",
            "suggested_bot_level": 6,
            "intro_closed": 5,
        }
    )
    if fresh_catalog:
        for item in db_session.exec(select(ContentItem)).all():
            db_session.delete(item)
        db_session.commit()
    restored = client.post("/v1/imports", json=archive, headers=headers)
    assert restored.status_code == 200, restored.text
    row = db_session.get(PlayerOnboarding, PLAYER_ID)
    assert row is not None
    assert row.model_dump() == {
        "player_id": PLAYER_ID,
        "status": "not_offered",
        "declared_level": None,
        "suggested_bot_level": None,
        "mapping_version": None,
        "intro_started_at": None,
        "skipped_at": None,
        "completed_at": None,
    }
    progress = client.get(f"/v1/players/{PLAYER_ID}/onboarding", headers=headers).json()
    assert progress["intro_started"] == progress["intro_closed"] == 0
    assert progress["preferred_bot_level"] == 1
    exported = client.post("/v1/exports", json={"player_id": PLAYER_ID}, headers=headers).json()
    assert exported["puzzle_rating_events"] == archive["puzzle_rating_events"]
    assert exported["skill_states"] == archive["skill_states"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("player_id", "foreign-player"),
        ("player_id", 3),
        ("player_id", None),
        ("status", "placed"),
        ("status", True),
        ("status", []),
        ("declared_level", "not-sure"),
        ("declared_level", 2),
        ("declared_level", {}),
        ("declared_level", None),
        ("suggested_bot_level", "3"),
        ("suggested_bot_level", 3.0),
        ("suggested_bot_level", True),
        ("suggested_bot_level", 0),
        ("suggested_bot_level", 9),
        ("suggested_bot_level", 6),
        ("suggested_bot_level", None),
        ("mapping_version", None),
        ("mapping_version", "declared-level-v2"),
        ("mapping_version", 1),
        ("mapping_version", {}),
        ("intro_started_at", None),
        ("intro_started_at", 0),
        ("intro_started_at", True),
        ("intro_started_at", "2026-01-01"),
        ("intro_started_at", "not-a-date"),
        ("intro_started_at", "2999-01-01T00:00:00Z"),
        ("skipped_at", "2999-01-01T00:00:00Z"),
        ("skipped_at", 1),
        ("completed_at", "2999-01-01T00:00:00Z"),
        ("completed_at", False),
        ("intro_started", 5),
        ("intro_closed", 5),
        ("intro_target", 5),
        ("progress", {"closed": 5}),
        ("unknown", None),
    ],
)
def test_restore_rejects_raw_onboarding_tampering_atomically(
    client: TestClient,
    db_session: Session,
    field: str,
    value: object,
) -> None:
    archive, headers = _owner_archive(client, db_session, active_index=4)
    _delete(client, headers)
    archive["onboarding"][field] = value
    _reject(client, db_session, archive, headers)


@pytest.mark.parametrize(
    "field",
    [
        "player_id",
        "status",
        "declared_level",
        "suggested_bot_level",
        "mapping_version",
        "intro_started_at",
        "skipped_at",
        "completed_at",
    ],
)
def test_restore_rejects_partial_onboarding_records(
    client: TestClient,
    db_session: Session,
    field: str,
) -> None:
    archive, headers = _owner_archive(client, db_session, active_index=4)
    _delete(client, headers)
    archive["onboarding"].pop(field)
    _reject(client, db_session, archive, headers)


@pytest.mark.parametrize(
    "invalid_kind",
    [
        "missing-completion",
        "wrong-completion",
        "sixth-completion",
        "false-in-progress",
        "pre-clock-only",
        "skipped-before-start",
        "skipped-before-completion",
    ],
)
def test_restore_rejects_inconsistent_completion_and_timestamp_order(
    client: TestClient,
    db_session: Session,
    invalid_kind: str,
) -> None:
    archive, headers = _owner_archive(client, db_session)
    _delete(client, headers)
    row = archive["onboarding"]
    if invalid_kind == "missing-completion":
        row["completed_at"] = None
    elif invalid_kind == "wrong-completion":
        row["completed_at"] = row["intro_started_at"]
    elif invalid_kind == "sixth-completion":
        row["completed_at"] = next(
            run["completed_at"] for run in archive["puzzle_runs"] if run["id"] == "onboarding-run-7"
        )
    elif invalid_kind == "false-in-progress":
        row.update(status="in_progress", completed_at=None)
    elif invalid_kind == "pre-clock-only":
        row["intro_started_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    elif invalid_kind == "skipped-before-start":
        row.update(status="skipped", skipped_at=(datetime.now(UTC) - timedelta(days=4)).isoformat())
    else:
        row.update(status="skipped", skipped_at=row["intro_started_at"])
    _reject(client, db_session, archive, headers)


@pytest.mark.parametrize("status", ["pending", "not_offered"])
def test_unplaced_status_rejects_declaration_history(
    client: TestClient,
    db_session: Session,
    status: str,
) -> None:
    archive, headers = _owner_archive(client, db_session, active_index=4)
    _delete(client, headers)
    archive["onboarding"]["status"] = status
    _reject(client, db_session, archive, headers)


@pytest.mark.parametrize("placed", [False, True])
def test_skip_roundtrip_preserves_identity_and_prior_declaration_without_inventing_progress(
    client: TestClient,
    db_session: Session,
    placed: bool,
) -> None:
    archive, headers = _owner_archive(client, db_session, status="skipped", placed=placed)
    _delete(client, headers)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    row = db_session.get(PlayerOnboarding, PLAYER_ID)
    assert row is not None and row.model_dump(mode="json") == archive["onboarding"]
    assert row.status == "skipped"
    assert (row.intro_started_at is not None) == placed
    assert row.completed_at is None


def test_completed_then_skipped_archive_retains_completion(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _owner_archive(client, db_session)
    skipped = client.post(
        f"/v1/players/{PLAYER_ID}/onboarding",
        headers=headers,
        json={"action": "skip"},
    )
    assert skipped.status_code == 200
    exported = client.post("/v1/exports", json={"player_id": PLAYER_ID}, headers=headers).json()
    assert exported["onboarding"]["status"] == "skipped"
    assert exported["onboarding"]["completed_at"] == archive["onboarding"]["completed_at"]
    _delete(client, headers)
    response = client.post("/v1/imports", json=exported, headers=headers)
    assert response.status_code == 200, response.text


def test_incomplete_first_five_cannot_be_replaced_by_later_closed_run(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _owner_archive(client, db_session, active_index=4)
    assert archive["onboarding"]["status"] == "in_progress"
    progress = client.get(f"/v1/players/{PLAYER_ID}/onboarding", headers=headers).json()
    assert progress["intro_started"] == 5 and progress["intro_closed"] == 4
    _delete(client, headers)
    valid = client.post("/v1/imports", json=archive, headers=headers)
    assert valid.status_code == 200, valid.text
    _delete(client, headers)
    archive["onboarding"].update(
        status="completed",
        completed_at=next(
            run["completed_at"] for run in archive["puzzle_runs"] if run["id"] == "onboarding-run-7"
        ),
    )
    _reject(client, db_session, archive, headers)


def test_onboarding_deletion_dry_run_rollback_and_audit_are_player_scoped(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, headers = _owner_archive(client, db_session)
    other = client.post("/v1/players", json={"id": "other-onboarding-owner"})
    assert other.status_code == 200
    other_row = db_session.get(PlayerOnboarding, "other-onboarding-owner")
    assert other_row is not None
    other_data = other_row.model_dump(mode="json")
    dry = client.request(
        "DELETE",
        f"/v1/players/{PLAYER_ID}/data",
        headers=headers,
        json={"dry_run": True},
    )
    assert dry.status_code == 200
    assert dry.json()["affected_rows"]["onboarding"] == 1
    assert db_session.get(PlayerOnboarding, PLAYER_ID) is not None
    assert db_session.exec(select(DeletionAudit)).all() == []

    def abort_commit() -> None:
        raise RuntimeError("injected onboarding deletion commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", abort_commit)
        with pytest.raises(RuntimeError, match="injected onboarding deletion commit failure"):
            _delete(client, headers)
    db_session.rollback()
    row = db_session.get(PlayerOnboarding, PLAYER_ID)
    assert row is not None and row.model_dump(mode="json") == archive["onboarding"]
    assert len(db_session.exec(select(PuzzleRatingEvent)).all()) == 7
    assert len(db_session.exec(select(SkillState)).all()) == 1
    assert db_session.exec(select(DeletionAudit)).all() == []
    deleted = _delete(client, headers)
    assert deleted["affected_rows"] == dry.json()["affected_rows"]
    db_session.expire_all()
    assert db_session.get(PlayerOnboarding, PLAYER_ID) is None
    audit = db_session.get(DeletionAudit, deleted["audit_id"])
    assert audit is not None and audit.affected_rows == deleted["affected_rows"]
    retained = db_session.get(PlayerOnboarding, "other-onboarding-owner")
    assert retained is not None and retained.model_dump(mode="json") == other_data
    assert db_session.exec(select(PuzzleRatingEvent)).all() == []
    assert db_session.exec(select(SkillState)).all() == []


def test_onboarding_migration_preserves_history_and_downgrade_only_drops_onboarding(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'onboarding-copy.db'}")
    config = _migration_config(engine)
    command.upgrade(config, "20261113_15")
    with Session(engine) as session:
        session.add_all(
            [
                Player(
                    id=PLAYER_ID, preferences={"declared_level": "advanced", "manual_bot_level": 4}
                ),
                Player(
                    id="second-legacy-owner", preferences={"onboarding": {"status": "completed"}}
                ),
            ]
        )
        session.commit()
        session.add(PlayerProfile(player_id=PLAYER_ID))
        session.commit()
        _seed_history(session, PLAYER_ID)
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}

    def snapshot() -> dict[str, list[tuple[Any, ...]]]:
        with engine.connect() as connection:
            return {
                table: sorted(
                    (tuple(row) for row in connection.execute(text(f'SELECT * FROM "{table}"'))),
                    key=repr,
                )
                for table in tables
            }

    before = snapshot()
    command.upgrade(config, "20261113_16")
    schema = inspect(engine)
    assert schema.get_pk_constraint("playeronboarding")["constrained_columns"] == ["player_id"]
    assert {fk["referred_table"] for fk in schema.get_foreign_keys("playeronboarding")} == {
        "player"
    }
    assert {column["name"] for column in schema.get_columns("playeronboarding")} == {
        "player_id",
        "status",
        "declared_level",
        "suggested_bot_level",
        "mapping_version",
        "intro_started_at",
        "skipped_at",
        "completed_at",
    }
    assert snapshot() == before
    with Session(engine) as session:
        rows = session.exec(select(PlayerOnboarding)).all()
        assert {row.player_id for row in rows} == {PLAYER_ID, "second-legacy-owner"}
        for row in rows:
            assert row.status == "not_offered"
            assert all(
                value is None
                for key, value in row.model_dump().items()
                if key not in {"player_id", "status"}
            )
    command.downgrade(config, "20261113_15")
    assert set(inspect(engine).get_table_names()) == tables | {"alembic_version"}
    assert snapshot() == before
    command.upgrade(config, "20261113_16")
    assert snapshot() == before
    with Session(engine) as session:
        assert all(
            row.status == "not_offered" for row in session.exec(select(PlayerOnboarding)).all()
        )
    engine.dispose()


@pytest.mark.parametrize(
    ("declared", "suggestion"),
    [
        ("beginner", 2),
        ("developing", 3),
        ("club", 5),
        ("advanced", 6),
        ("not_sure", 1),
    ],
)
def test_restore_accepts_each_declared_mapping_without_recalibrating_from_puzzles(
    client: TestClient,
    db_session: Session,
    declared: str,
    suggestion: int,
) -> None:
    archive, headers = _owner_archive(client, db_session, active_index=4)
    _delete(client, headers)
    archive["onboarding"].update(declared_level=declared, suggested_bot_level=suggestion)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    row = db_session.get(PlayerOnboarding, PLAYER_ID)
    assert row is not None and row.declared_level == declared
    assert row.suggested_bot_level == suggestion and row.completed_at is None


@pytest.mark.parametrize("status", ["pending", "not_offered"])
def test_fresh_unplaced_archive_retains_opt_in_status(
    client: TestClient,
    db_session: Session,
    status: str,
) -> None:
    archive, headers = _owner_archive(client, db_session, status=status, placed=False)
    _delete(client, headers)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    row = db_session.get(PlayerOnboarding, PLAYER_ID)
    assert row is not None and row.model_dump(mode="json") == archive["onboarding"]


@pytest.mark.parametrize("value", [None, True, False, "3", 3.0, 0, 9, [], {}])
def test_archive_manual_bot_choice_rejects_non_integer_or_out_of_range_values(
    client: TestClient,
    db_session: Session,
    value: object,
) -> None:
    archive, headers = _owner_archive(client, db_session)
    _delete(client, headers)
    archive["player"]["preferences"]["manual_bot_level"] = value
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert "manual bot choice" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Player, PLAYER_ID) is None
    assert db_session.get(PlayerOnboarding, PLAYER_ID) is None
    assert db_session.exec(select(PuzzleRatingEvent)).all() == []


@pytest.mark.parametrize("level", [1, 8])
def test_archive_manual_bot_choice_preserves_valid_override(
    client: TestClient,
    db_session: Session,
    level: int,
) -> None:
    archive, headers = _owner_archive(client, db_session)
    _delete(client, headers)
    archive["player"]["preferences"]["manual_bot_level"] = level
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    progress = client.get(f"/v1/players/{PLAYER_ID}/onboarding", headers=headers).json()
    assert progress["preferred_bot_level"] == progress["manual_bot_level"] == level
    assert progress["suggested_bot_level"] == 3


def test_skipped_without_declaration_cannot_claim_historical_completion(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _owner_archive(client, db_session, status="skipped", placed=False)
    _delete(client, headers)
    archive["onboarding"]["completed_at"] = archive["puzzle_runs"][0]["completed_at"]
    _reject(client, db_session, archive, headers)


def test_introductory_archive_continues_across_daily_sets(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _owner_archive(client, db_session)
    _delete(client, headers)
    later = deepcopy(archive["puzzle_daily_sets"][0])
    later["id"] = "next-day-onboarding-set"
    later["created_at"] = (
        datetime.fromisoformat(later["created_at"]) + timedelta(days=1)
    ).isoformat()
    later["utc_day"] = datetime.fromisoformat(later["created_at"]).date().isoformat()
    archive["puzzle_daily_sets"].append(later)
    moved = {"onboarding-run-5", "onboarding-run-6", "onboarding-run-7"}
    position = 0
    for slot in archive["puzzle_daily_slots"]:
        if slot["run_id"] in moved or slot["run_id"] is None:
            slot.update(set_id=later["id"], position=position)
            position += 1
            if slot["closed_at"] is not None:
                slot["closed_at"] = (
                    datetime.fromisoformat(slot["closed_at"]) + timedelta(days=1)
                ).isoformat()
    for run in archive["puzzle_runs"]:
        if run["id"] in moved:
            for key in ("started_at", "completed_at"):
                run[key] = (datetime.fromisoformat(run[key]) + timedelta(days=1)).isoformat()
    for attempt in archive["content_attempts"]:
        if attempt["id"] in moved:
            attempt["completed_at"] = (
                datetime.fromisoformat(attempt["completed_at"]) + timedelta(days=1)
            ).isoformat()
    for event in archive["puzzle_rating_events"]:
        if event["run_id"] in moved:
            event["created_at"] = (
                datetime.fromisoformat(event["created_at"]) + timedelta(days=1)
            ).isoformat()
    archive["onboarding"]["completed_at"] = (
        datetime.fromisoformat(archive["onboarding"]["completed_at"]) + timedelta(days=1)
    ).isoformat()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    progress = client.get(f"/v1/players/{PLAYER_ID}/onboarding", headers=headers).json()
    assert progress["intro_started"] == progress["intro_closed"] == 5
    assert progress["status"] == "completed"


@pytest.mark.parametrize("offset_hours", [2, -7])
@pytest.mark.parametrize("status", ["completed", "in_progress", "skipped"])
def test_restore_normalizes_offset_instants_before_persisting_intro_eligibility(
    client: TestClient,
    db_session: Session,
    status: str,
    offset_hours: int,
) -> None:
    archive, headers = _owner_archive(
        client,
        db_session,
        active_index=4 if status == "in_progress" else None,
    )
    if status == "skipped":
        skipped = client.post(
            f"/v1/players/{PLAYER_ID}/onboarding",
            headers=headers,
            json={"action": "skip"},
        )
        assert skipped.status_code == 200
        archive = client.post(
            "/v1/exports",
            json={"player_id": PLAYER_ID},
            headers=headers,
        ).json()
    original = deepcopy(archive["onboarding"])
    rating = deepcopy(archive["puzzle_rating"])
    _delete(client, headers)
    for key in ("intro_started_at", "skipped_at", "completed_at"):
        raw = original[key]
        if raw is not None:
            instant = datetime.fromisoformat(raw).replace(tzinfo=UTC)
            archive["onboarding"][key] = instant.astimezone(
                timezone(timedelta(hours=offset_hours)),
            ).isoformat()
    restored = client.post("/v1/imports", json=archive, headers=headers)
    assert restored.status_code == 200, restored.text
    progress = client.get(f"/v1/players/{PLAYER_ID}/onboarding", headers=headers).json()
    assert progress["status"] == status
    assert progress["intro_started"] == 5
    assert progress["intro_closed"] == (4 if status == "in_progress" else 5)
    exported = client.post("/v1/exports", json={"player_id": PLAYER_ID}, headers=headers)
    assert exported.status_code == 200
    canonical = exported.json()
    assert canonical["puzzle_rating"] == rating
    for key in ("intro_started_at", "skipped_at", "completed_at"):
        assert canonical["onboarding"][key] == original[key]
    _delete(client, headers)
    again = client.post("/v1/imports", json=canonical, headers=headers)
    assert again.status_code == 200, again.text
    assert (
        client.get(
            f"/v1/players/{PLAYER_ID}/onboarding",
            headers=headers,
        ).json()["intro_closed"]
        == progress["intro_closed"]
    )
