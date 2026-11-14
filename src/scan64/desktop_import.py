"""Cancellable desktop imports staged away from the live application database."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from scan64.content.models import ContentItem  # noqa: F401 -- register catalog metadata
from scan64.content.puzzle_import import ImportSummary, import_puzzles


class ImportCancelled(Exception):
    """The user cancelled before the catalog was committed."""


def run_import(
    engine: Engine, path: Path, cancel: Event, progress: Callable[[str], None]
) -> ImportSummary:
    batches = 0

    def checkpoint() -> None:
        if cancel.is_set():
            raise ImportCancelled()

    class ImportSession(Session):
        def commit(self) -> None:
            nonlocal batches
            checkpoint()
            super().commit()
            batches += 1
            progress(f"Importing puzzles… processed batch {batches}")

    progress("Reading puzzle file… Cancel leaves your catalog unchanged.")
    checkpoint()
    with TemporaryDirectory(prefix="scan64-puzzles-") as temporary:
        staging = create_engine(f"sqlite:///{Path(temporary) / 'catalog.db'}")
        SQLModel.metadata.create_all(staging, tables=[SQLModel.metadata.tables["contentitem"]])
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    "ATTACH DATABASE ? AS puzzle_stage", (str(Path(temporary) / "catalog.db"),)
                )
                try:
                    # Snapshot only the catalog, not the player's private database.
                    # Writes here target the staging file; no live write lock is held
                    # while parsing/validating the potentially very large CSV.
                    connection.exec_driver_sql(
                        "INSERT INTO puzzle_stage.contentitem SELECT * FROM main.contentitem"
                    )
                    connection.exec_driver_sql(
                        "CREATE TABLE puzzle_stage.original AS SELECT id, payload "
                        "FROM puzzle_stage.contentitem"
                    )
                    connection.commit()
                    with ImportSession(staging) as session:
                        summary = import_puzzles(session, path)
                    checkpoint()
                    progress("Saving puzzle catalog…")
                    # A single set-based merge keeps the live write transaction short.
                    # The snapshot preserves original identities, filters and ordering;
                    # unchanged rows are never rewritten.
                    connection.exec_driver_sql(
                        "INSERT INTO main.contentitem "
                        "SELECT staged.* FROM puzzle_stage.contentitem AS staged "
                        "LEFT JOIN puzzle_stage.original AS original ON original.id = staged.id "
                        "WHERE staged.domain = 'puzzles' AND "
                        "(original.id IS NULL OR original.payload != staged.payload) "
                        "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
                        "difficulty_estimate = excluded.difficulty_estimate"
                    )
                    checkpoint()
                    connection.commit()
                    return summary
                finally:
                    connection.rollback()
                    connection.exec_driver_sql("DETACH DATABASE puzzle_stage")
                    connection.commit()
        finally:
            staging.dispose()
