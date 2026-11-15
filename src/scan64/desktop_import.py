"""Cancellable desktop imports staged away from the live application database."""

from _thread import LockType
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from scan64.content.models import ContentItem  # noqa: F401 -- register catalog metadata
from scan64.content.puzzle_import import (
    ImportProgress,
    ImportSummary,
    SourceProvenance,
    import_puzzles,
    local_provenance,
)


class ImportCancelled(Exception):
    """The user cancelled before the catalog was committed."""


def run_import(
    engine: Engine,
    path: Path,
    cancel: Event,
    progress: Callable[[str], None],
    *,
    on_progress: Callable[[ImportProgress], None] | None = None,
    commit_lock: LockType | None = None,
    committed: Event | None = None,
    provenance: SourceProvenance | None = None,
    before_commit: Callable[[ImportSummary], None] | None = None,
) -> ImportSummary:
    batches = 0

    def checkpoint() -> None:
        if cancel.is_set():
            raise ImportCancelled()

    def report(update: ImportProgress) -> None:
        checkpoint()
        if on_progress is not None:
            on_progress(update)
        checkpoint()

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
                    if provenance is not None:
                        local_provenance(path, report)
                    with ImportSession(staging) as session:
                        summary = import_puzzles(
                            session, path, provenance=provenance, progress=report
                        )
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
                    # Serialize cancellation with the irreversible commit. Once committed,
                    # a late Cancel cannot report that the catalog was left unchanged.
                    with commit_lock if commit_lock is not None else nullcontext():
                        checkpoint()
                        if before_commit is not None:
                            before_commit(summary)
                        connection.commit()
                        if committed is not None:
                            committed.set()
                    return summary
                finally:
                    connection.rollback()
                    connection.exec_driver_sql("DETACH DATABASE puzzle_stage")
                    connection.commit()
        finally:
            staging.dispose()
