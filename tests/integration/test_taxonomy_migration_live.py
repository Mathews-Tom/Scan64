from datetime import UTC, datetime

from sqlmodel import Session, SQLModel, create_engine

from scan64.learning.diagnosis.taxonomy.migration import (
    MigrationRule,
    TaxonomyMigrationTable,
    migrate_live_rows,
)
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule


def test_live_taxonomy_migration_remaps_and_retires_rows_idempotently() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    table = TaxonomyMigrationTable(
        version="v2",
        rules={
            "tactics.old": MigrationRule(
                old_id="tactics.old", new_id="tactics.new", reason="Renamed"
            ),
            "tactics.retired": MigrationRule(
                old_id="tactics.retired", new_id=None, reason="Invalid taxonomy code"
            ),
        },
    )

    with Session(engine) as session:
        session.add(SkillState(player_id="player", concept_code="tactics.old", alpha=3, beta=4))
        session.add(
            SkillState(
                player_id="player",
                concept_code="tactics.new",
                alpha=4,
                beta=6,
                prior_alpha=2,
                prior_beta=2,
                last_updated=now,
            )
        )
        session.add(SkillState(player_id="player", concept_code="tactics.retired"))
        session.add(
            ReviewSchedule(
                player_id="player", item_id="lesson-old", skill_id="tactics.old", next_review_at=now
            )
        )
        session.add(
            ReviewSchedule(
                player_id="player",
                item_id="lesson-retired",
                skill_id="tactics.retired",
                next_review_at=now,
            )
        )
        session.add(
            ProfileObservation(
                player_id="player",
                game_id="game",
                position_id="position",
                skill_id="tactics.old",
                observed_at=now,
            )
        )
        session.add(
            ProfileObservation(
                player_id="player",
                game_id="game",
                position_id="position",
                skill_id="tactics.new",
                observed_at=now,
            )
        )
        session.add(
            ProfileObservation(
                player_id="player",
                game_id="retired-game",
                position_id="position",
                skill_id="tactics.retired",
                observed_at=now,
            )
        )
        session.add(
            ProfileObservation(
                player_id="player",
                game_id="free-game",
                position_id="position",
                skill_id="tactics.old",
                observed_at=now,
            )
        )
        session.commit()

        migrate_live_rows(session, table)
        migrate_live_rows(session, table)

        renamed = session.get(SkillState, ("player", "tactics.new"))
        retired = session.get(SkillState, ("player", "tactics.retired"))
        old = session.get(SkillState, ("player", "tactics.old"))
        assert renamed is not None
        assert old is not None and old.retirement_reason == "Renamed"
        assert renamed.alpha == 6
        assert renamed.beta == 9
        assert renamed.prior_alpha == 2
        assert renamed.prior_beta == 2
        assert renamed.last_updated is not None
        assert renamed.last_updated.replace(tzinfo=UTC) == now
        assert retired is not None and retired.retirement_reason == "Invalid taxonomy code"
        renamed_schedule = session.get(ReviewSchedule, ("player", "lesson-old"))
        assert renamed_schedule is not None
        assert renamed_schedule.skill_id == "tactics.new"
        retired_schedule = session.get(ReviewSchedule, ("player", "lesson-retired"))
        assert retired_schedule is not None
        assert retired_schedule.retirement_reason == "Invalid taxonomy code"
        renamed_observation = session.get(
            ProfileObservation, ("player", "game", "position", "tactics.new")
        )
        assert renamed_observation is not None
        old_observation = session.get(
            ProfileObservation, ("player", "game", "position", "tactics.old")
        )
        assert old_observation is not None
        assert old_observation.retired_at is not None
        assert old_observation.retirement_reason == "Renamed to tactics.new"
        retired_observation = session.get(
            ProfileObservation, ("player", "retired-game", "position", "tactics.retired")
        )
        assert retired_observation is not None
        assert retired_observation.retired_at is not None
        assert retired_observation.retirement_reason == "Invalid taxonomy code"
        remapped_observation = session.get(
            ProfileObservation, ("player", "free-game", "position", "tactics.new")
        )
        assert remapped_observation is not None
        assert remapped_observation.retired_at is None
        assert (
            session.get(ProfileObservation, ("player", "free-game", "position", "tactics.old"))
            is None
        )
