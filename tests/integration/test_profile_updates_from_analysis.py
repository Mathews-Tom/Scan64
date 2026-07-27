from datetime import UTC, datetime

from sqlmodel import Session, SQLModel, create_engine

from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.profiling.profile_update import apply_analysis_observation


def test_analysis_observation_is_idempotent_per_game_position_and_skill() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    observed_at = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)

    with Session(engine) as session:
        assert apply_analysis_observation(
            session, "player", "game-1", "position-1", "tactics.fork", 1200, observed_at
        )
        session.commit()
        first = session.get(SkillState, ("player", "tactics.fork"))
        assert first is not None
        first_mastery = first.expected_mastery

        assert not apply_analysis_observation(
            session, "player", "game-1", "position-1", "tactics.fork", 1200, observed_at
        )
        session.commit()
        repeated = session.get(SkillState, ("player", "tactics.fork"))
        assert repeated is not None
        assert repeated.expected_mastery == first_mastery
        assert session.get(ProfileObservation, ("player", "game-1", "position-1", "tactics.fork"))
