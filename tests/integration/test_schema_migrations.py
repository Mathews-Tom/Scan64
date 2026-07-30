from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from scan64.persistence.database import _migration_config, migrate_database


def test_fresh_sqlite_database_is_built_from_the_migration_chain(tmp_path: Path) -> None:
    database_engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")

    migrate_database(database_engine)

    with database_engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert {
        "game",
        "persistedlessonopportunity",
        "profileobservation",
        "lessonattempt",
        "transfermeasurement",
    } <= tables
    assert revision == "20260730_06"
    opportunity_columns = {
        column["name"]: column
        for column in inspect(database_engine).get_columns("persistedlessonopportunity")
    }
    assert opportunity_columns["source_position_id"]["nullable"] is False
    assert opportunity_columns["verification_status"]["nullable"] is False
    assert opportunity_columns["verification_error"]["nullable"] is True


def test_populated_legacy_sqlite_database_is_stamped_without_data_loss(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    database_engine = create_engine(f"sqlite:///{database_path}")

    with database_engine.begin() as connection:
        connection.execute(text("CREATE TABLE game (id TEXT PRIMARY KEY, pgn TEXT NOT NULL)"))
        connection.execute(
            text(
                "CREATE TABLE playsession ("
                "id TEXT PRIMARY KEY, "
                "player_id TEXT NOT NULL, "
                "game_id TEXT NOT NULL"
                ")"
            )
        )
        connection.execute(
            text("INSERT INTO game (id, pgn) VALUES (:id, :pgn)"),
            {"id": "legacy-game", "pgn": "1. e4 e5"},
        )
        connection.execute(
            text(
                "INSERT INTO playsession (id, player_id, game_id) "
                "VALUES (:id, :player_id, :game_id)"
            ),
            {
                "id": "legacy-session",
                "player_id": "legacy-player",
                "game_id": "legacy-game",
            },
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
        tables = set(inspect(connection).get_table_names())
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert stored_pgn == "1. e4 e5"
    assert owner_column == "legacy-player"
    assert "persistedlessonopportunity" not in tables
    assert revision == "20260730_06"


def _create_pre_m40_database(database_engine: Engine) -> tuple[str, str, str]:
    game_id = uuid4().hex
    position_id = uuid4().hex
    opportunity_id = uuid4().hex
    with database_engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20260728_04')"))
        connection.execute(
            text(
                "CREATE TABLE game (id CHAR(32) PRIMARY KEY, pgn TEXT NOT NULL, "
                "owner_player_id TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE position (id CHAR(32) PRIMARY KEY, game_id CHAR(32), "
                "fen TEXT NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE persistedlessonopportunity ("
                "id CHAR(32) PRIMARY KEY, game_id CHAR(32) NOT NULL REFERENCES game(id), "
                "player_id TEXT, created_at DATETIME, lesson_spec JSON NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_persistedlessonopportunity_game_id "
                "ON persistedlessonopportunity (game_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX ix_persistedlessonopportunity_player_id "
                "ON persistedlessonopportunity (player_id)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE lessonattempt ("
                "id CHAR(32) PRIMARY KEY, opportunity_id CHAR(32), "
                "FOREIGN KEY(opportunity_id) REFERENCES persistedlessonopportunity(id))"
            )
        )
        connection.execute(
            text("INSERT INTO game (id, pgn, owner_player_id) VALUES (:id, '', 'player')"),
            {"id": game_id},
        )
    return game_id, position_id, opportunity_id


def _lesson_spec_with_source(fen: str) -> str:
    return json.dumps({"source": {"kind": "player_game", "fen": fen}})


def test_m40_migration_backfills_exact_source_position(tmp_path: Path) -> None:
    database_engine = create_engine(f"sqlite:///{tmp_path / 'pre_m40.db'}")
    game_id, position_id, opportunity_id = _create_pre_m40_database(database_engine)
    fen = "8/8/8/8/8/8/8/K6k w - - 0 1"
    with database_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO position (id, game_id, fen) VALUES (:id, :game_id, :fen)"),
            {"id": position_id, "game_id": game_id, "fen": fen},
        )
        connection.execute(
            text("INSERT INTO position (id, game_id, fen) VALUES (:id, :game_id, :fen)"),
            {"id": uuid4().hex, "game_id": uuid4().hex, "fen": fen},
        )
        connection.execute(
            text(
                "INSERT INTO persistedlessonopportunity "
                "(id, game_id, lesson_spec) VALUES (:id, :game_id, :lesson_spec)"
            ),
            {
                "id": opportunity_id,
                "game_id": game_id,
                "lesson_spec": _lesson_spec_with_source(fen),
            },
        )

    migrate_database(database_engine)

    with database_engine.connect() as connection:
        source_position_id = connection.execute(
            text("SELECT source_position_id FROM persistedlessonopportunity WHERE id = :id"),
            {"id": opportunity_id},
        ).scalar_one()
    assert source_position_id == position_id
    with database_engine.begin() as connection:
        config = _migration_config(database_engine)
        config.attributes["connection"] = connection
        command.downgrade(config, "20260728_04")

    with database_engine.connect() as connection:
        inspector = inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("persistedlessonopportunity")}
        index_names = {
            index["name"] for index in inspector.get_indexes("persistedlessonopportunity")
        }
        lesson_attempt_foreign_keys = inspector.get_foreign_keys("lessonattempt")
        stored_lesson_spec = connection.execute(
            text("SELECT lesson_spec FROM persistedlessonopportunity WHERE id = :id"),
            {"id": opportunity_id},
        ).scalar_one()
    assert "source_position_id" not in columns
    assert "ix_persistedlessonopportunity_source_position_id" not in index_names
    assert {
        "ix_persistedlessonopportunity_game_id",
        "ix_persistedlessonopportunity_player_id",
    } <= index_names
    assert any(
        foreign_key["referred_table"] == "persistedlessonopportunity"
        for foreign_key in lesson_attempt_foreign_keys
    )
    assert stored_lesson_spec == _lesson_spec_with_source(fen)


def test_m40_migration_rejects_ambiguous_source_position(tmp_path: Path) -> None:
    database_engine = create_engine(f"sqlite:///{tmp_path / 'ambiguous_pre_m40.db'}")
    game_id, first_position_id, opportunity_id = _create_pre_m40_database(database_engine)
    fen = "8/8/8/8/8/8/8/K6k w - - 0 1"
    with database_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO position (id, game_id, fen) VALUES (:id, :game_id, :fen)"),
            {"id": first_position_id, "game_id": game_id, "fen": fen},
        )
        connection.execute(
            text("INSERT INTO position (id, game_id, fen) VALUES (:id, :game_id, :fen)"),
            {"id": uuid4().hex, "game_id": game_id, "fen": fen},
        )
        connection.execute(
            text(
                "INSERT INTO persistedlessonopportunity "
                "(id, game_id, lesson_spec) VALUES (:id, :game_id, :lesson_spec)"
            ),
            {
                "id": opportunity_id,
                "game_id": game_id,
                "lesson_spec": _lesson_spec_with_source(fen),
            },
        )

    with pytest.raises(RuntimeError, match="resolved to 2 positions"):
        migrate_database(database_engine)


@pytest.mark.parametrize(
    ("lesson_spec", "error"),
    [
        (_lesson_spec_with_source("missing-position"), "resolved to 0 positions"),
        (json.dumps({"source": {}}), "has no source FEN"),
        ("[]", "invalid lesson specification"),
    ],
)
def test_m40_migration_rejects_unresolved_or_malformed_source_position(
    tmp_path: Path, lesson_spec: str, error: str
) -> None:
    database_engine = create_engine(f"sqlite:///{tmp_path / 'invalid_pre_m40.db'}")
    game_id, _, opportunity_id = _create_pre_m40_database(database_engine)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO persistedlessonopportunity "
                "(id, game_id, lesson_spec) VALUES (:id, :game_id, :lesson_spec)"
            ),
            {
                "id": opportunity_id,
                "game_id": game_id,
                "lesson_spec": lesson_spec,
            },
        )

    with pytest.raises(RuntimeError, match=error):
        migrate_database(database_engine)
