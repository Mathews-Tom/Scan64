from pathlib import Path
from shutil import copy2

from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from scan64.api.models import Player, PlayerOnboarding, PlayerProfile
from scan64.content.models import StudySession
from scan64.content.puzzle_practice_models import (
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
)
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import _migration_config
from tests.integration.test_puzzle_lifecycle import puzzle_item


def test_onboarding_populated_copy_up_down_up_preserves_measured_history(tmp_path: Path) -> None:
    original, copied = tmp_path / "m65-original.db", tmp_path / "m66-copy.db"
    engine = create_engine(f"sqlite:///{original}")
    config = _migration_config(engine)
    command.upgrade(config, "20261113_15")
    with Session(engine) as session:
        session.add(Player(id="migration-player", preferences={"manual_bot_level": 7}))
        session.add(PlayerProfile(player_id="migration-player"))
        session.add(
            SkillState(player_id="migration-player", concept_code="unmapped", alpha=4.25, beta=2.75)
        )
        session.add(puzzle_item())
        study = StudySession(player_id="migration-player", domain="puzzles")
        daily = PuzzleDailySet(player_id="migration-player", utc_day="2026-11-12")
        session.add(study)
        session.add(daily)
        session.flush()
        run = PuzzleRun(
            player_id="migration-player",
            item_id="lichess:lifecycle",
            session_id=study.id,
            state="solved",
            fen="unused-for-migration",
        )
        session.add(run)
        session.flush()
        slot = PuzzleDailySlot(
            player_id="migration-player",
            set_id=daily.id,
            position=0,
            item_id="lichess:lifecycle",
            item_rating=1200,
            reason="exploration",
            widening="400",
            first_exposure=True,
            run_id=run.id,
        )
        session.add(slot)
        session.flush()
        session.add(PuzzleRating(player_id="migration-player", rating=1216, rated_count=1))
        session.add(
            PuzzleRatingEvent(
                player_id="migration-player",
                run_id=run.id,
                slot_id=slot.id,
                item_id=run.item_id,
                sequence=1,
                item_rating=1200,
                before=1200,
                after=1216,
                score=1,
            )
        )
        session.commit()
    engine.dispose()
    copy2(original, copied)
    engine = create_engine(f"sqlite:///{copied}")
    config = _migration_config(engine)
    for cycle in range(2):
        command.upgrade(config, "head")
        with Session(engine) as session:
            onboarding = session.get(PlayerOnboarding, "migration-player")
            assert onboarding.status == "not_offered"
            assert onboarding.intro_started_at is None and onboarding.declared_level is None
            assert session.get(PlayerProfile, "migration-player").rating == 1500
            state = session.get(SkillState, ("migration-player", "unmapped"))
            assert (state.alpha, state.beta) == (4.25, 2.75)
            assert session.get(PuzzleRating, "migration-player").rating == 1216
            assert session.get(Player, "migration-player").preferences == {"manual_bot_level": 7}
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM puzzleratingevent")).scalar_one() == 1
            )
            assert connection.execute(text("SELECT count(*) FROM puzzlerun")).scalar_one() == 1
        if cycle == 0:
            command.downgrade(config, "20261113_15")
            assert "playeronboarding" not in inspect(engine).get_table_names()
    source = create_engine(f"sqlite:///{original}")
    assert "playeronboarding" not in inspect(source).get_table_names()
    with source.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "20261113_15"
        )
    source.dispose()
    engine.dispose()
