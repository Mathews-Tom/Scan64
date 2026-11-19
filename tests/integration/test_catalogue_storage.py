"""M86 migration, indexed reads and real concurrent catalogue-write acceptance."""

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2
from threading import Event
from unittest.mock import patch

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect
from sqlmodel import Session, select

import scan64.persistence.database as database
from scan64.api.app import app
from scan64.api.models import Player
from scan64.content.models import ContentItem, PuzzleTheme
from scan64.content.puzzle_import import ImportSummary
from scan64.content.puzzle_modes import select_mode_item, source_themes
from scan64.content.puzzle_practice import _pool, daily_set, slots_for
from scan64.content.puzzle_practice_models import PuzzleDailySet, PuzzleDailySlot, PuzzleRating
from scan64.content.puzzle_themes import replace_puzzle_themes
from scan64.desktop_import import run_import
from scan64.learning.profiling.models import SkillState
from tests.integration.test_puzzle_lifecycle import puzzle_item
from tests.integration.test_puzzle_runs import _auth
from tests.unit.test_puzzle_import import puzzle, save_csv


def test_each_new_sqlite_connection_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    engine = database.create_database_engine(f"sqlite:///{tmp_path / 'wal.db'}")
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
    finally:
        engine.dispose()


def test_non_sqlite_driver_receives_no_sqlite_options() -> None:
    with (
        patch.object(database, "create_engine") as factory,
        patch.object(event, "listen") as listen,
    ):
        result = database.create_database_engine("postgresql://localhost/scan64")
        factory.assert_called_once_with("postgresql://localhost/scan64", echo=False)
        listen.assert_not_called()
        assert result is factory.return_value


def test_real_daily_and_theme_queries_use_indexes_without_catalogue_scan(
    db_session: Session,
) -> None:
    db_session.add(Player(id="indexed"))
    db_session.add(
        SkillState(
            player_id="indexed", concept_code="threat_processing.missed_capture", alpha=1, beta=4
        )
    )
    items = []
    for index in range(80):
        item = puzzle_item()
        item.id = f"indexed:{index:03}"
        item.payload = {
            **item.payload,
            "themes": ["hangingPiece" if index % 2 else "fork"],
            "import_sequence": index,
        }
        item.difficulty_estimate = 1200
        db_session.add(item)
        items.append(item)
    replace_puzzle_themes(db_session, items)
    db_session.commit()
    connection = db_session.connection()
    queries: list[tuple[str, object]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "FROM contentitem" in statement:
            queries.append((statement, parameters))

    event.listen(connection, "before_cursor_execute", capture)
    try:
        assert _pool("indexed", 1200, datetime.now(UTC), db_session)
        day = daily_set("indexed", db_session, commit_new=False)
        assert select_mode_item("indexed", day, "fork", db_session) is not None
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert len(queries) >= 3
    theme_queries = 0
    for statement, parameters in queries:
        plan = "\n".join(
            row[3]
            for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)
        )
        assert "SCAN contentitem" not in plan, plan
        # The due-review join has no rating window and uses its existing indexes.
        if "ORDER BY" in statement and "JOIN reviewschedule" not in statement:
            assert "ix_contentitem_domain_difficulty" in plan, (statement, plan)
        if "puzzletheme" in statement:
            theme_queries += 1
            assert "ix_puzzletheme_theme_item" in plan, plan
    assert theme_queries >= 2
    with patch.object(db_session, "exec", wraps=db_session.exec) as execute:
        assert source_themes(db_session) == ["fork", "hangingPiece"]
        sql = str(execute.call_args.args[0])
        assert "contentitem" not in sql and "DISTINCT" in sql
        plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql).all()
        assert any("ix_puzzletheme_theme_item" in row[3] for row in plan)


def test_v080_copy_upgrade_downgrade_reupgrade_preserves_today_and_themes(tmp_path: Path) -> None:
    original = tmp_path / "v080.db"
    copied = tmp_path / "upgrade-copy.db"
    old_engine = database.create_database_engine(f"sqlite:///{original}")
    config = database._migration_config(old_engine)
    command.upgrade(config, "20261113_17")
    now = datetime.now(UTC)
    with Session(old_engine) as session:
        session.add(Player(id="existing", preferences={"preserved": True}))
        session.add(PuzzleRating(player_id="existing", rating=1432, rated_count=12))
        day = PuzzleDailySet(player_id="existing", utc_day=now.date().isoformat(), created_at=now)
        session.add(day)
        session.flush()
        for index in range(12):
            item = puzzle_item()
            item.id = f"existing:{index}"
            item.payload = {**item.payload, "themes": ["fork", "pin", "fork", "", 42]}
            session.add(item)
            session.flush()
            if index < 10:
                session.add(
                    PuzzleDailySlot(
                        player_id="existing",
                        set_id=day.id,
                        position=index,
                        item_id=item.id,
                        item_rating=1500,
                        reason="exploration",
                        widening="400",
                        first_exposure=True,
                    )
                )
        session.commit()
        expected_slots = [slot.model_dump() for slot in slots_for(day, session)]
        expected_day = day.model_dump()
        expected_themes = sorted(
            {
                theme
                for item in session.exec(select(ContentItem))
                for theme in item.payload["themes"]
                if isinstance(theme, str) and theme
            }
        )
    old_engine.dispose()  # closes/checkpoints WAL before copying, never copy a live main file
    copy2(original, copied)
    engine = database.create_database_engine(f"sqlite:///{copied}")
    config = database._migration_config(engine)
    try:
        for phase in ("upgrade", "reupgrade"):
            command.upgrade(config, "head")
            with Session(engine) as session:
                assert source_themes(session) == expected_themes
                assert len(session.exec(select(PuzzleTheme)).all()) == 24
                restored_day = daily_set("existing", session, now)
                assert restored_day.model_dump() == expected_day
                assert [
                    slot.model_dump() for slot in slots_for(restored_day, session)
                ] == expected_slots
                rating = session.get(PuzzleRating, "existing")
                assert rating is not None and (rating.rating, rating.rated_count) == (1432, 12)
                assert session.get(Player, "existing").preferences == {"preserved": True}
                revision = (
                    session.connection()
                    .exec_driver_sql("SELECT version_num FROM alembic_version")
                    .scalar_one()
                )
            print(
                f"{phase}: revision {revision}; 12 puzzles, 24 theme rows; "
                "Today and ratings preserved"
            )
            if phase == "upgrade":
                command.downgrade(config, "20261113_17")
                assert "puzzletheme" not in inspect(engine).get_table_names()
                assert "ix_contentitem_domain_difficulty" not in {
                    index["name"] for index in inspect(engine).get_indexes("contentitem")
                }
                print("downgrade: revision 20261113_17; derived table/index removed")
        source = database.create_database_engine(f"sqlite:///{original}")
        try:
            with source.connect() as connection:
                assert (
                    connection.exec_driver_sql(
                        "SELECT version_num FROM alembic_version"
                    ).scalar_one()
                    == "20261113_17"
                )
        finally:
            source.dispose()
    finally:
        engine.dispose()


def test_today_reads_while_desktop_import_holds_live_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = database.create_database_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")
    database.migrate_database(engine)
    monkeypatch.setattr(database, "engine", engine)

    def session_override() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    previous = app.dependency_overrides.get(database.get_session)
    app.dependency_overrides[database.get_session] = session_override
    holding_write, release = Event(), Event()

    def before_commit(summary: ImportSummary) -> None:
        assert summary.inserted == 300
        holding_write.set()
        assert release.wait(10), "Today read never completed"

    try:
        with TestClient(app) as client:
            auth = _auth(client)
            before = client.get("/v1/players/puzzle-player/habit", headers=auth)
            assert before.status_code == 200
            path = save_csv(tmp_path, [puzzle(f"concurrent{i}") for i in range(300)])
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_import, engine, path, Event(), lambda _: None, before_commit=before_commit
                )
                try:
                    assert holding_write.wait(10), (
                        "Import never acquired its live write transaction"
                    )
                    during = client.get("/v1/players/puzzle-player/habit", headers=auth)
                    assert during.status_code == 200, during.text
                    assert during.json() == before.json()
                    with Session(engine) as session:
                        assert session.exec(select(ContentItem)).all() == []
                finally:
                    release.set()
                assert future.result(timeout=10).inserted == 300
            with Session(engine) as session:
                assert len(session.exec(select(PuzzleTheme)).all()) == 600
    finally:
        if previous is None:
            app.dependency_overrides.pop(database.get_session, None)
        else:
            app.dependency_overrides[database.get_session] = previous
        engine.dispose()
