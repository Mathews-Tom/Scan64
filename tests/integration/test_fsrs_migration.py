from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from scan64.desktop_backups import VERSION_MARKER, prepare_desktop_backup
from scan64.learning.scheduling.spaced_repetition import (
    ReviewSchedule,
    ScheduleReviewObservation,
)
from scan64.persistence.database import _migration_config, create_database_engine, migrate_database

_NOW = datetime(2026, 7, 14, 12, tzinfo=UTC)
_OLD_COLUMNS = (
    "player_id,item_id,next_review_at,last_reviewed_at,interval_days,ease_factor,"
    "skill_id,retired_at,retirement_reason"
)
_NEW_COLUMNS = (
    "player_id,item_id,next_review_at,last_reviewed_at,"
    "legacy_interval_days AS interval_days,legacy_ease_factor AS ease_factor,"
    "skill_id,retired_at,retirement_reason"
)


def _legacy_database(path: Path) -> Engine:
    engine = create_database_engine(f"sqlite:///{path}")
    command.upgrade(_migration_config(engine), "20261117_18")
    with engine.begin() as connection:
        for item, due, last, retired in (
            ("due", _NOW - timedelta(days=2), _NOW - timedelta(days=3), None),
            ("future", _NOW + timedelta(days=7), None, None),
            ("retired", _NOW - timedelta(days=1), _NOW - timedelta(days=2), _NOW),
        ):
            connection.execute(
                text(
                    "INSERT INTO reviewschedule "
                    f"({_OLD_COLUMNS}) VALUES "
                    "(:player,:item,:due,:last,:interval,:ease,:skill,:retired,:reason)"
                ),
                {
                    "player": "migration-owner",
                    "item": item,
                    "due": due,
                    "last": last,
                    "interval": 2.75,
                    "ease": 2.1,
                    "skill": "calculation",
                    "retired": retired,
                    "reason": "kept-retired" if retired is not None else None,
                },
            )
    return engine


def _rows(engine: Engine, columns: str) -> list[dict[str, object]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(f"SELECT {columns} FROM reviewschedule ORDER BY item_id")
            ).mappings()
        ]


def test_upgrade_preserves_due_retirement_and_unused_downgrade(tmp_path: Path) -> None:
    engine = _legacy_database(tmp_path / "legacy.db")
    config = _migration_config(engine)
    try:
        original = _rows(engine, _OLD_COLUMNS)
        command.upgrade(config, "20261119_19")
        assert _rows(engine, _NEW_COLUMNS) == original
        with Session(engine) as session:
            due = session.get(ReviewSchedule, ("migration-owner", "due"))
            future = session.get(ReviewSchedule, ("migration-owner", "future"))
            retired = session.get(ReviewSchedule, ("migration-owner", "retired"))
            assert due is not None and due.is_due(_NOW)
            assert future is not None and not future.is_due(_NOW)
            assert retired is not None and retired.retirement_reason == "kept-retired"
            assert retired.retired_at is not None
            assert all(
                row.fsrs_state == 1
                and row.fsrs_step == 0
                and row.fsrs_stability is None
                and row.fsrs_difficulty is None
                for row in (due, future, retired)
            )
            assert session.exec(select(ScheduleReviewObservation)).all() == []
        converted = _rows(engine, "*")
        migrate_database(engine)
        assert _rows(engine, "*") == converted
        assert "sparringattempt" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "20261119_20"
            )
        command.downgrade(config, "20261119_19")
        assert _rows(engine, "*") == converted
        assert "sparringattempt" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            assert session.exec(select(ScheduleReviewObservation)).all() == []
        command.downgrade(config, "20261117_18")
        assert _rows(engine, _OLD_COLUMNS) == original
        migrate_database(engine)
        assert _rows(engine, _NEW_COLUMNS) == original
    finally:
        engine.dispose()


def test_downgrade_refuses_to_discard_review_state_and_observations(tmp_path: Path) -> None:
    engine = _legacy_database(tmp_path / "reviewed.db")
    try:
        config = _migration_config(engine)
        command.upgrade(config, "20261119_19")
        with Session(engine) as session:
            schedule = session.get(ReviewSchedule, ("migration-owner", "due"))
            assert schedule is not None
            observation = schedule.update(
                True,
                _NOW,
                hints_used=1,
                elapsed_ms=42_000,
                source_kind="lesson",
                source_id="accepted-migration-review",
            )
            session.add(schedule)
            session.add(observation)
            session.commit()
            expected_due = schedule.next_review_at
            expected_stability = schedule.fsrs_stability
            observation_id = observation.id
            expected_observation = observation.model_dump(mode="json")
        expected_schedules = _rows(engine, "*")
        for revision in ("20261119_20", "20261119_19"):
            if revision == "20261119_20":
                migrate_database(engine)
            else:
                command.downgrade(config, revision)
            assert _rows(engine, "*") == expected_schedules
            with Session(engine) as session:
                restored_observation = session.exec(select(ScheduleReviewObservation)).one()
                assert restored_observation.model_dump(mode="json") == expected_observation
            with engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == revision
                )
        assert "sparringattempt" not in inspect(engine).get_table_names()
        with pytest.raises(RuntimeError, match="FSRS reviews exist"):
            command.downgrade(config, "20261117_18")
        assert _rows(engine, "*") == expected_schedules
        with Session(engine) as session:
            schedule = session.get(ReviewSchedule, ("migration-owner", "due"))
            observation = session.get(ScheduleReviewObservation, observation_id)
            assert schedule is not None and schedule.next_review_at == expected_due
            assert schedule.fsrs_stability == expected_stability
            assert observation is not None and observation.rating == 2
            assert observation.hints_used == 1 and observation.elapsed_ms == 42_000
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "20261119_19"
            )
    finally:
        engine.dispose()


def test_existing_desktop_snapshot_retains_pre_fsrs_database(tmp_path: Path) -> None:
    engine = _legacy_database(tmp_path / "scan64.db")
    try:
        original = _rows(engine, _OLD_COLUMNS)
        (tmp_path / VERSION_MARKER).write_text("0.10.0\n", encoding="utf-8")
        backup = prepare_desktop_backup(tmp_path, "0.11.0")
        (snapshot,) = (tmp_path / "backups").glob("scan64-*-before-*.db")
        migrate_database(engine)
        assert _rows(engine, _NEW_COLUMNS) == original
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "20261119_20"
            )
        with sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20261117_18",
            )
            assert [tuple(row.values()) for row in original] == connection.execute(
                f"SELECT {_OLD_COLUMNS} FROM reviewschedule ORDER BY item_id"
            ).fetchall()
        backup.record_successful_startup()
        assert (tmp_path / VERSION_MARKER).read_text(encoding="utf-8").strip() == "0.11.0"
    finally:
        engine.dispose()
