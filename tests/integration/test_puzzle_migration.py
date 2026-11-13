from pathlib import Path

import chess
from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from scan64.api.models import Player
from scan64.content.models import ContentItem, StudySession
from scan64.content.puzzle_runs import PuzzleRun
from scan64.persistence.database import _migration_config


def test_puzzle_upgrade_downgrade_preserves_existing_catalog_and_player(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'populated.db'}")
    config = _migration_config(engine)
    command.upgrade(config, "20261112_11")
    with Session(engine) as session:
        player = Player(id="migration-owner", preferences={"theme": "dark"})
        item = ContentItem(
            id="existing-content",
            domain="tactics",
            provenance="fixture",
            licence="CC0-1.0",
            payload={"preserved": "metadata"},
        )
        study = StudySession(id="existing-session", player_id=player.id, domain="tactics")
        session.add_all([player, item, study])
        session.commit()
    command.upgrade(config, "head")
    assert "puzzlerun" in inspect(engine).get_table_names()
    indexes = inspect(engine).get_indexes("puzzlerun")
    assert any(
        index["name"] == "uq_puzzlerun_active_player" and index["unique"] for index in indexes
    )
    assert len(inspect(engine).get_foreign_keys("puzzlerun")) == 3
    with Session(engine) as session:
        session.add(
            PuzzleRun(
                player_id="migration-owner",
                item_id="existing-content",
                session_id="existing-session",
                fen=chess.STARTING_FEN,
            )
        )
        session.commit()
    command.downgrade(config, "20261112_11")
    assert "puzzlerun" not in inspect(engine).get_table_names()
    with Session(engine) as session:
        assert session.get(Player, "migration-owner").preferences == {"theme": "dark"}
        assert session.get(ContentItem, "existing-content").payload == {"preserved": "metadata"}
        assert session.get(StudySession, "existing-session") is not None
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM puzzlerun")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "20261113_17"
        )
    engine.dispose()
