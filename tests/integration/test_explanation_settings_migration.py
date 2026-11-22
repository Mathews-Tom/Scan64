from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlmodel import Session

from scan64.api.models import Player
from scan64.explanations.settings import OwnerExplanationSettings
from scan64.learning.repertoire import Repertoire, parse_repertoire_pgn
from scan64.persistence.database import _migration_config, create_database_engine


def test_explanation_settings_preserve_owner_and_allow_backup_recovery(tmp_path: Path) -> None:
    database = tmp_path / "active.sqlite"
    backup = tmp_path / "pre-migration.sqlite"
    engine = create_database_engine(f"sqlite:///{database}")
    try:
        config = _migration_config(engine)
        command.upgrade(config, "20261122_23")
        with Session(engine) as session:
            session.add(Player(id="existing-owner", preferences={"manual_bot_level": 3}))
            session.commit()
            title, pgn, tree = parse_repertoire_pgn("1. e4 e5 *")[0]
            repertoire = Repertoire(
                player_id="existing-owner", title=title, pgn=pgn, tree=tree.model_dump()
            )
            repertoire_id = repertoire.id
            session.add(repertoire)
            session.commit()
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup)) as copy:
            source.backup(copy)

        command.upgrade(config, "20261122_24")
        with Session(engine) as session:
            owner = session.get(Player, "existing-owner")
            assert owner is not None and owner.preferences == {"manual_bot_level": 3}
            current = session.get(Repertoire, repertoire_id)
            assert current is not None and current.tree == tree.model_dump()
            assert session.get(OwnerExplanationSettings, owner.id) is None
            session.add(OwnerExplanationSettings(player_id=owner.id, model="local-proof:latest"))
            session.commit()

        with pytest.raises(RuntimeError, match="local explanation consent"):
            command.downgrade(config, "20261122_23")
        with Session(engine) as session:
            assert session.get(OwnerExplanationSettings, "existing-owner") is not None

        # Rehearse recovery on a separate copy, not by overwriting the upgraded database.
        recovery_engine = create_database_engine(f"sqlite:///{backup}")
        try:
            assert "ownerexplanationsettings" not in inspect(recovery_engine).get_table_names()
            with Session(recovery_engine) as session:
                owner = session.get(Player, "existing-owner")
                assert owner is not None and owner.preferences == {"manual_bot_level": 3}
                current = session.get(Repertoire, repertoire_id)
                assert current is not None and current.tree == tree.model_dump()
        finally:
            recovery_engine.dispose()
    finally:
        engine.dispose()
