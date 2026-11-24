from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import ConnectionPoolEntry
from sqlmodel import Session

from scan64.api.models import Player
from scan64.desktop_backups import VERSION_MARKER, prepare_desktop_backup
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.lesson_packs.models import LessonPack, PackLesson
from scan64.persistence.database import _migration_config, create_database_engine

NOW = datetime(2026, 11, 24, tzinfo=UTC)
EXAMPLE = Path(__file__).parents[2] / "docs/lesson-packs-example.json"


def pack(owner: str = "owner", slug: str = "community-example") -> LessonPack:
    return LessonPack(
        player_id=owner,
        pack_id=slug,
        title="Community example",
        author="Community author",
        licence="CC0-1.0",
        version="1.0",
        sha256="0" * 64,
        installed_at=NOW,
    )


def enable_foreign_keys(connection: sqlite3.Connection, _: ConnectionPoolEntry) -> None:
    connection.execute("PRAGMA foreign_keys=ON")


def test_desktop_snapshot_upgrade_downgrade_reupgrade_on_copy_preserves_progress(
    tmp_path: Path,
) -> None:
    original_path = tmp_path / "scan64.db"
    original = create_database_engine(f"sqlite:///{original_path}")
    working: Engine | None = None
    try:
        original_config = _migration_config(original)
        command.upgrade(original_config, "20261122_24")
        with Session(original) as session:
            session.add(Player(id="owner", preferences={"preserved": True}))
            session.add(
                SkillState(
                    player_id="owner", concept_code="tactics.hanging_piece", alpha=4.5, beta=2.5
                )
            )
            session.add(
                ReviewSchedule(player_id="owner", item_id="existing-lesson", next_review_at=NOW)
            )
            session.commit()
        (tmp_path / VERSION_MARKER).write_text("0.12.2\n", encoding="utf-8")
        backup = prepare_desktop_backup(tmp_path, "1.0.0")
        (snapshot,) = (tmp_path / "backups").glob("scan64-*-before-*.db")
        copy_path = tmp_path / "migration-copy.db"
        shutil.copy2(snapshot, copy_path)
        working = create_database_engine(f"sqlite:///{copy_path}")
        config = _migration_config(working)
        with working.connect() as connection:
            original_skills = connection.execute(text("SELECT * FROM skillstate")).fetchall()
            original_schedules = connection.execute(text("SELECT * FROM reviewschedule")).fetchall()
            original_players = connection.execute(text("SELECT * FROM player")).fetchall()
        command.upgrade(config, "20261124_25")
        assert {"lessonpack", "packlesson"} <= set(inspect(working).get_table_names())
        # Downgrade intentionally discards only reinstallable pack data, not progress.
        imported = pack()
        payload = json.loads(EXAMPLE.read_bytes())["lessons"][0]
        with Session(working) as session:
            session.add(imported)
            session.flush()
            session.add(
                PackLesson(
                    pack_id=imported.id,
                    lesson_id=payload["id"],
                    title=payload["title"],
                    payload=payload,
                    state="verified",
                )
            )
            session.commit()
        command.downgrade(config, "20261122_24")
        assert "lessonpack" not in inspect(working).get_table_names()
        assert "packlesson" not in inspect(working).get_table_names()
        command.upgrade(config, "20261124_25")
        with working.connect() as connection:
            assert (
                connection.execute(text("SELECT * FROM skillstate")).fetchall() == original_skills
            )
            assert (
                connection.execute(text("SELECT * FROM reviewschedule")).fetchall()
                == original_schedules
            )
            assert connection.execute(text("SELECT * FROM player")).fetchall() == original_players
            assert connection.execute(text("SELECT COUNT(*) FROM lessonpack")).scalar_one() == 0
            assert connection.execute(text("SELECT COUNT(*) FROM packlesson")).scalar_one() == 0
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM schedulereviewobservation")
                ).scalar_one()
                == 0
            )
        # The existing desktop backup API also remains usable by real startup.
        command.upgrade(original_config, "20261124_25")
        backup.record_successful_startup()
        recovered = tmp_path / "recovered-copy.db"
        shutil.copy2(snapshot, recovered)
        with sqlite3.connect(f"file:{recovered}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20261122_24",
            )
            assert connection.execute("SELECT item_id FROM reviewschedule").fetchall() == [
                ("existing-lesson",)
            ]
            assert connection.execute("SELECT alpha, beta FROM skillstate").fetchall() == [
                (4.5, 2.5)
            ]
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name IN ('lessonpack', 'packlesson')"
                ).fetchall()
                == []
            )
        assert "lessonpack" in inspect(original).get_table_names()
        assert (tmp_path / VERSION_MARKER).read_text(encoding="utf-8") == "1.0.0\n"
    finally:
        if working is not None:
            working.dispose()
        original.dispose()


def test_pack_migration_constraints_enforce_owner_slug_and_parent_identity(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'constraints.db'}")
    event.listen(engine, "connect", enable_foreign_keys)
    try:
        command.upgrade(_migration_config(engine), "20261124_25")
        inspector = inspect(engine)
        assert inspector.get_foreign_keys("lessonpack")[0]["referred_table"] == "player"
        assert inspector.get_foreign_keys("packlesson")[0]["referred_table"] == "lessonpack"
        assert any(
            constraint["column_names"] == ["player_id", "pack_id"]
            for constraint in inspector.get_unique_constraints("lessonpack")
        )
        assert any(
            constraint["column_names"] == ["pack_id", "lesson_id"]
            for constraint in inspector.get_unique_constraints("packlesson")
        )
        with Session(engine) as session:
            session.add_all([Player(id="owner"), Player(id="other")])
            session.commit()
            first = pack()
            second = pack("other")
            session.add_all([first, second])
            session.commit()
            first_id, second_id = first.id, second.id
            session.add(pack())
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
            session.add(pack("nonexistent-owner"))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
            payload = json.loads(EXAMPLE.read_bytes())["lessons"][0]
            for parent_id in (first_id, second_id):
                session.add(
                    PackLesson(
                        pack_id=parent_id,
                        lesson_id="same-lesson",
                        title="Example",
                        payload=payload,
                    )
                )
            session.commit()
            session.add(
                PackLesson(
                    pack_id=first_id, lesson_id="same-lesson", title="Duplicate", payload=payload
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
            session.add(
                PackLesson(pack_id=uuid4(), lesson_id="orphan", title="Orphan", payload=payload)
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
            with pytest.raises(IntegrityError):
                session.execute(text("UPDATE packlesson SET state = 'verified', failing_ply = 0"))
            session.rollback()
    finally:
        engine.dispose()
