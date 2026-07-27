from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlmodel import Session, create_engine

from scan64.learning.diagnosis.taxonomy.migration import (
    DEFAULT_MIGRATION_TABLE,
    migrate_live_rows,
)
from scan64.persistence.models import load_models

sqlite_file_name = "database.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, echo=False, connect_args=connect_args)


def _migration_config(database_engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    config.set_main_option(
        "sqlalchemy.url",
        database_engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    return config


def _has_user_tables(connection: Connection) -> bool:
    return any(
        table_name != "alembic_version" for table_name in inspect(connection).get_table_names()
    )


def migrate_database(database_engine: Engine) -> None:
    load_models()
    with database_engine.begin() as connection:
        config = _migration_config(database_engine)
        config.attributes["connection"] = connection
        if not inspect(connection).has_table("alembic_version") and _has_user_tables(
            connection
        ):
            command.stamp(config, "20260727_01")
        command.upgrade(config, "head")


def create_db_and_tables() -> None:
    migrate_database(engine)
    with Session(engine) as session:
        migrate_live_rows(session, DEFAULT_MIGRATION_TABLE)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
