from pathlib import Path
from shutil import copy2

from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from scan64.api.models import Player
from scan64.content.models import ContentItem
from scan64.content.puzzle_practice import daily_set
from scan64.content.puzzle_practice_models import PuzzleDailySlot, PuzzleRating
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import _migration_config
from tests.integration.test_puzzle_lifecycle import puzzle_item


def test_populated_copy_upgrade_downgrade_upgrade_preserves_legacy_data(tmp_path: Path) -> None:
    original = tmp_path / "m64-before.db"
    copied = tmp_path / "m64-copy.db"
    engine = create_engine(f"sqlite:///{original}")
    config = _migration_config(engine)
    command.upgrade(config, "20261112_13")
    with Session(engine) as session:
        session.add(Player(id="migration-player", preferences={"preserved": True}))
        session.add(puzzle_item())
        session.add(
            SkillState(player_id="migration-player", concept_code="unmapped", alpha=4.25, beta=2.75)
        )
        session.commit()
    engine.dispose()
    copy2(original, copied)
    engine = create_engine(f"sqlite:///{copied}")
    config = _migration_config(engine)
    command.upgrade(config, "head")
    assert {"puzzlerating", "puzzledailyset", "puzzledailyslot", "puzzleratingevent"} <= set(
        inspect(engine).get_table_names()
    )
    with Session(engine) as session:
        day = daily_set("migration-player", session)
        assert day.player_rating == 1200
        assert session.get(PuzzleRating, "migration-player").rated_count == 0
        from sqlmodel import select

        assert len(session.exec(select(PuzzleDailySlot)).all()) == 1
    command.downgrade(config, "20261112_13")
    assert "puzzledailyslot" not in inspect(engine).get_table_names()
    with Session(engine) as session:
        assert session.get(Player, "migration-player").preferences == {"preserved": True}
        assert session.get(ContentItem, "lichess:lifecycle").domain == "puzzles"
        state = session.get(SkillState, ("migration-player", "unmapped"))
        assert (state.alpha, state.beta) == (4.25, 2.75)
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM puzzlerating")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM contentitem")).scalar_one() == 1
    source = create_engine(f"sqlite:///{original}")
    with source.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "20261112_13"
        )
    assert "puzzlerating" not in inspect(source).get_table_names()
    source.dispose()
    engine.dispose()
