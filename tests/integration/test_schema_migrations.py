from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from scan64.persistence.database import migrate_database


def test_fresh_sqlite_database_is_built_from_the_migration_chain(tmp_path: Path) -> None:
    database_engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")

    migrate_database(database_engine)

    with database_engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert {"game", "persistedlessonopportunity", "transfermeasurement"} <= tables
    assert revision == "20260727_02"


def test_populated_legacy_sqlite_database_is_stamped_without_data_loss(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    database_engine = create_engine(f"sqlite:///{database_path}")

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE game ("
                "id TEXT PRIMARY KEY, "
                "pgn TEXT NOT NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE persistedlessonopportunity ("
                "id TEXT PRIMARY KEY, "
                "game_id TEXT NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "lesson_spec JSON NOT NULL"
                ")"
            )
        )
        connection.execute(
            text("INSERT INTO game (id, pgn) VALUES (:id, :pgn)"),
            {"id": "legacy-game", "pgn": "1. e4 e5"},
        )

    migrate_database(database_engine)
    migrate_database(database_engine)

    with database_engine.connect() as connection:
        stored_pgn = connection.execute(
            text("SELECT pgn FROM game WHERE id = :id"),
            {"id": "legacy-game"},
        ).scalar_one()
        owner_column = connection.execute(
            text("SELECT owner_player_id FROM game WHERE id = :id"),
            {"id": "legacy-game"},
        ).scalar_one()
        opportunity_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(persistedlessonopportunity)"))
        }
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert stored_pgn == "1. e4 e5"
    assert owner_column is None
    assert "player_id" in opportunity_columns
    assert revision == "20260727_02"
