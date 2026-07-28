from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from scan64.content.models import LessonAttempt
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule

# Session fatigue reflects the sitting in progress, not lifetime history: a
# bounded recent window, not an unbounded one (system design §21.2).
RECENT_ATTEMPT_WINDOW = timedelta(hours=2)


@dataclass
class PlayerSessionState:
    """
    The real profile signals a training session is composed from (M38 / G16):
    active skill mastery, active review schedules, and recent server-verified
    attempt history.

    Retired `SkillState` and `ReviewSchedule` rows are historical (M34) and
    must never re-enter session composition; ungraded opening-mission
    attempts are excluded from `recent_verified_attempts` because M37 leaves
    them without a server-verified outcome.
    """

    skills_by_concept: dict[str, SkillState] = field(default_factory=dict)
    active_reviews: dict[str, ReviewSchedule] = field(default_factory=dict)
    recent_verified_attempts: list[LessonAttempt] = field(default_factory=list)

    def skill_for(self, skill_id: str | None) -> SkillState | None:
        """The active (non-retired) SkillState for a concept, or None if unobserved."""
        if skill_id is None:
            return None
        return self.skills_by_concept.get(skill_id)

    def recent_attempt_stats(self) -> tuple[int, float]:
        """(attempt count, error rate) over recent server-verified attempts."""
        attempts = self.recent_verified_attempts
        if not attempts:
            return 0, 0.0
        failed = sum(1 for attempt in attempts if attempt.success is False)
        return len(attempts), failed / len(attempts)


def load_player_session_state(
    db: Session, player_id: str, now: datetime | None = None
) -> PlayerSessionState:
    """Load the active profile state a training session request composes from."""
    now = now or datetime.now(UTC)

    skills = db.exec(select(SkillState).where(SkillState.player_id == player_id)).all()
    skills_by_concept = {
        skill.concept_code: skill for skill in skills if skill.retired_at is None
    }

    schedules = db.exec(
        select(ReviewSchedule).where(ReviewSchedule.player_id == player_id)
    ).all()
    active_reviews = {
        schedule.item_id: schedule for schedule in schedules if schedule.retired_at is None
    }

    recent_verified_attempts = db.exec(
        select(LessonAttempt)
        .where(LessonAttempt.player_id == player_id)
        .where(LessonAttempt.grading_status == "verified")
        .where(LessonAttempt.completed_at >= now - RECENT_ATTEMPT_WINDOW)
    ).all()

    return PlayerSessionState(
        skills_by_concept=skills_by_concept,
        active_reviews=active_reviews,
        recent_verified_attempts=list(recent_verified_attempts),
    )
