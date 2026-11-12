from datetime import UTC, datetime

from sqlmodel import Session, SQLModel, create_engine

from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.profiling.profile_update import (
    apply_analysis_observation,
    apply_lesson_attempt,
)


def test_analysis_observation_is_idempotent_per_game_position_and_skill() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    observed_at = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)

    with Session(engine) as session:
        assert apply_analysis_observation(
            session,
            "player",
            "game-1",
            "position-1",
            "board_awareness.hanging_piece",
            1200,
            observed_at,
        )
        session.commit()
        first = session.get(SkillState, ("player", "board_awareness.hanging_piece"))
        assert first is not None
        first_mastery = first.expected_mastery

        assert not apply_analysis_observation(
            session,
            "player",
            "game-1",
            "position-1",
            "board_awareness.hanging_piece",
            1200,
            observed_at,
        )
        session.commit()
        repeated = session.get(SkillState, ("player", "board_awareness.hanging_piece"))
        assert repeated is not None
        assert repeated.expected_mastery == first_mastery
        assert session.get(
            ProfileObservation, ("player", "game-1", "position-1", "board_awareness.hanging_piece")
        )


def test_untrusted_diagnoses_never_change_or_delete_existing_skills(db_session: Session) -> None:
    observed_at = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    old = SkillState(player_id="player", concept_code="tactics.pin", alpha=3, beta=2)
    db_session.add(old)
    db_session.commit()
    assert not apply_analysis_observation(
        db_session, "player", "game", "position", "tactics.pin", 1200, observed_at
    )
    assert (
        apply_lesson_attempt(db_session, "player", "tactics.pin", True, False, 1200, observed_at)
        == "skipped_untrusted"
    )
    db_session.commit()
    unchanged = db_session.get(SkillState, ("player", "tactics.pin"))
    assert unchanged is not None and unchanged.alpha == 3 and unchanged.beta == 2
    assert db_session.get(ProfileObservation, ("player", "game", "position", "tactics.pin")) is None
