from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlmodel import Session, SQLModel, create_engine

from scan64.learning.profiling.models import SkillState
from scan64.learning.profiling.profile_update import apply_analysis_observation
from scan64.persistence.models import load_models

# create_all needs every table that a foreign key names, so register all models.
load_models()


# Each example builds a fresh SQLite schema; its time is not what this test measures.
@settings(deadline=None)
@given(st.integers(min_value=2, max_value=10), st.sampled_from([1200, 1900]))
def test_repeated_diagnoses_monotonically_lower_mastery_and_uncertainty(
    observation_count: int, rating: int
) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    observed_at = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)

    with Session(engine) as session:
        previous_mastery = 1.0
        previous_uncertainty = 1.0
        for index in range(observation_count):
            assert apply_analysis_observation(
                session,
                "player",
                f"game-{index}",
                "position-1",
                "board_awareness.hanging_piece",
                rating,
                observed_at,
            )
            session.commit()
            skill = session.get(SkillState, ("player", "board_awareness.hanging_piece"))
            assert skill is not None
            assert skill.expected_mastery < previous_mastery
            assert skill.uncertainty < previous_uncertainty
            previous_mastery = skill.expected_mastery
            previous_uncertainty = skill.uncertainty
