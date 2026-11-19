"""Legacy hint facts survive conversion; disclosed new help cannot be downgraded away."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import chess
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from scan64.api.models import Player
from scan64.chess.games.assistance import prefix_hash
from scan64.chess.games.models import PlaySession
from scan64.persistence.database import _migration_config

FLAGS = ("candidate_used", "guard_used")


def _reveal(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(connection.execute(sa.text("SELECT * FROM sessionhintreveal")).mappings().one())


@pytest.fixture
def legacy_database(tmp_path: Path) -> Iterator[tuple[Engine, Config, dict[str, Any]]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-assistance.sqlite'}")
    config = _migration_config(engine)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "20261117_18")
        assert not set(FLAGS) & {
            column["name"] for column in sa.inspect(engine).get_columns("sessionhintreveal")
        }
        with Session(engine) as session:
            session.add(Player(id="legacy-assistance-owner", preferences={"kept": True}))
            play = PlaySession(
                player_id="legacy-assistance-owner",
                opponent_config={"assistance_generation": "7"},
                assistance_tracking_version=1,
            )
            session.add(play)
            session.commit()
            play_id = play.id.hex
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO sessionhintreveal "
                    "(session_id, player_id, prefix_hash, canonical_id, ply, initial_fen, moves, "
                    "highest_step) VALUES (:session_id, :player_id, :prefix_hash, :canonical_id, "
                    ":ply, :initial_fen, :moves, :highest_step)"
                ),
                {
                    "session_id": play_id,
                    "player_id": "legacy-assistance-owner",
                    "prefix_hash": prefix_hash(chess.STARTING_FEN, []),
                    "canonical_id": chess.Board().epd(),
                    "ply": 1,
                    "initial_fen": chess.STARTING_FEN,
                    "moves": "[]",
                    "highest_step": 2,
                },
            )
        yield engine, config, _reveal(engine)
    finally:
        engine.dispose()


def test_unused_upgrade_and_downgrade_preserve_exact_legacy_rows_and_references(
    legacy_database: tuple[Engine, Config, dict[str, Any]],
) -> None:
    engine, config, before = legacy_database
    foreign_keys = sa.inspect(engine).get_foreign_keys("sessionhintreveal")
    command.upgrade(config, "head")
    after = _reveal(engine)
    assert {key: value for key, value in after.items() if key not in FLAGS} == before
    assert after["candidate_used"] == 0 and after["guard_used"] == 0
    command.downgrade(config, "20261117_18")
    assert _reveal(engine) == before
    assert sa.inspect(engine).get_foreign_keys("sessionhintreveal") == foreign_keys
    command.upgrade(config, "head")
    with Session(engine) as session:
        owner = session.get(Player, "legacy-assistance-owner")
        assert owner is not None and owner.preferences == {"kept": True}
        play = session.get(PlaySession, UUID(before["session_id"]))
        assert play is not None and play.opponent_config == {"assistance_generation": "7"}


@pytest.mark.parametrize("flag", FLAGS)
def test_downgrade_refuses_to_erase_disclosed_candidate_or_guard_provenance(
    legacy_database: tuple[Engine, Config, dict[str, Any]], flag: str
) -> None:
    engine, config, before = legacy_database
    command.upgrade(config, "head")
    table = sa.table("sessionhintreveal", sa.column(flag, sa.Boolean), sa.column("highest_step"))
    with engine.begin() as connection:
        connection.execute(table.update().values({flag: True, "highest_step": 0}))
    with pytest.raises(RuntimeError):
        command.downgrade(config, "20261117_18")
    after = _reveal(engine)
    assert after[flag] == 1 and after["highest_step"] == 0
    assert after["session_id"] == before["session_id"]
    assert set(FLAGS) <= after.keys()
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == ("20261119_21")


@pytest.mark.parametrize("flag", FLAGS)
def test_retry_after_partial_sqlite_ddl_keeps_existing_disclosure(
    legacy_database: tuple[Engine, Config, dict[str, Any]], flag: str
) -> None:
    engine, config, before = legacy_database
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        operations.add_column(
            "sessionhintreveal",
            sa.Column(flag, sa.Boolean, nullable=False, server_default=sa.false()),
        )
        table = sa.table("sessionhintreveal", sa.column(flag, sa.Boolean))
        connection.execute(table.update().values({flag: True}))
    command.upgrade(config, "head")
    after = _reveal(engine)
    assert after[flag] == 1
    assert after[next(name for name in FLAGS if name != flag)] == 0
    assert {key: value for key, value in after.items() if key not in FLAGS} == before
