from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session

from scan64.desktop_backups import VERSION_MARKER, prepare_desktop_backup
from scan64.learning.repertoire import (
    Repertoire,
    RepertoireAttempt,
    parse_repertoire_pgn,
    repertoire_item_id,
)
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import _migration_config, create_database_engine

NOW = datetime(2026, 11, 22, tzinfo=UTC)


def test_clean_upgrade_unused_downgrade_reupgrade_preserves_other_schedules(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'copy.db'}")
    try:
        config = _migration_config(engine)
        command.upgrade(config, "20261119_22")
        with Session(engine) as session:
            session.add(
                ReviewSchedule(player_id="owner", item_id="lesson-kept", next_review_at=NOW)
            )
            session.commit()
        with engine.connect() as connection:
            before = connection.execute(text("SELECT * FROM reviewschedule")).fetchall()
        command.upgrade(config, "20261122_23")
        assert {"repertoire", "repertoireattempt"} <= set(inspect(engine).get_table_names())
        command.downgrade(config, "20261119_22")
        assert "repertoire" not in inspect(engine).get_table_names()
        command.upgrade(config, "20261122_23")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM reviewschedule")).fetchall() == before
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM schedulereviewobservation")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("practised", [False, True])
def test_downgrade_refuses_imported_content_and_practice_without_losing_rows(
    tmp_path: Path, practised: bool
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'copy.db'}")
    try:
        config = _migration_config(engine)
        command.upgrade(config, "20261122_23")
        title, pgn, tree = parse_repertoire_pgn("1. e4 e5 *")[0]
        repertoire = Repertoire(
            player_id="owner", title=title, pgn=pgn, tree=tree.model_dump(), created_at=NOW
        )
        with Session(engine) as session:
            session.add(repertoire)
            session.commit()
            if practised:
                attempt_id = uuid4()
                schedule = ReviewSchedule(
                    player_id="owner",
                    item_id=repertoire_item_id(repertoire.id, 0),
                    next_review_at=NOW,
                )
                observation = schedule.update(
                    True,
                    NOW,
                    hints_used=None,
                    elapsed_ms=None,
                    source_kind="repertoire",
                    source_id=str(attempt_id),
                )
                session.add(
                    RepertoireAttempt(
                        id=attempt_id,
                        player_id="owner",
                        repertoire_id=repertoire.id,
                        node_id=0,
                        sequence=1,
                        submitted_move="e2e4",
                        success=True,
                        completed_at=NOW,
                    )
                )
                session.add(schedule)
                session.flush()
                session.add(observation)
                session.commit()
        with pytest.raises(RuntimeError, match="Cannot downgrade"):
            command.downgrade(config, "20261119_22")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "20261122_23"
            )
            assert connection.execute(text("SELECT COUNT(*) FROM repertoire")).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM repertoireattempt")
            ).scalar_one() == int(practised)
            assert connection.execute(
                text("SELECT COUNT(*) FROM schedulereviewobservation")
            ).scalar_one() == int(practised)
    finally:
        engine.dispose()


def test_desktop_pre_migration_backup_recovers_on_separate_copy(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'scan64.db'}")
    try:
        config = _migration_config(engine)
        command.upgrade(config, "20261119_22")
        with Session(engine) as session:
            session.add(
                ReviewSchedule(player_id="owner", item_id="existing-review", next_review_at=NOW)
            )
            session.commit()
        (tmp_path / VERSION_MARKER).write_text("0.11.0\n", encoding="utf-8")
        backup = prepare_desktop_backup(tmp_path, "0.12.0")
        (snapshot,) = (tmp_path / "backups").glob("scan64-*-before-*.db")
        command.upgrade(config, "20261122_23")
        backup.record_successful_startup()
        recovered = tmp_path / "recovered-copy.db"
        shutil.copy2(snapshot, recovered)
        with sqlite3.connect(f"file:{recovered}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "20261119_22",
            )
            assert connection.execute("SELECT item_id FROM reviewschedule").fetchall() == [
                ("existing-review",)
            ]
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'repertoire'"
                ).fetchall()
                == []
            )
        # The current upgraded database was not overwritten by recovery.
        assert "repertoire" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
