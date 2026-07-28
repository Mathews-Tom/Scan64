from dataclasses import dataclass

from scan64.learning.profiling.models import SkillState


@dataclass
class PriorityFactors:
    """
    Priority factors for session composition.
    All terms are bounded to [0, 1].
    """

    review_due: float = 0.0
    weakness_severity: float = 0.0
    recurrence_probability: float = 0.0
    curriculum_relevance: float = 0.0
    transfer_need: float = 0.0
    user_interest: float = 0.0
    recent_overexposure: float = 0.0

    def compute_priority(self, session_fatigue: float = 0.0) -> float:
        """
        Compute overall priority score.
        score = sum(positive_factors) - recent_overexposure - session_fatigue
        """
        score = (
            self.review_due
            + self.weakness_severity
            + self.recurrence_probability
            + self.curriculum_relevance
            + self.transfer_need
            + self.user_interest
            - self.recent_overexposure
            - session_fatigue
        )
        return max(0.0, score)


def compute_session_fatigue(
    consecutive_lessons: int,
    baseline_response_time_ms: float,
    rolling_response_time_ms: float,
) -> float:
    """
    Compute session fatigue based on consecutive lessons and response-time degradation.
    Returns a value bounded to [0, 1].

    - Base fatigue from lesson count (e.g. maxes out at 20 lessons).
    - Degradation fatigue based on how much slower the rolling response time
      is compared to baseline.
    """
    if consecutive_lessons < 0:
        consecutive_lessons = 0

    # Linearly increase up to 20 lessons
    lesson_factor = min(1.0, consecutive_lessons / 20.0)

    rt_degradation = 0.0
    if baseline_response_time_ms > 0 and rolling_response_time_ms > baseline_response_time_ms:
        # 100% degradation (taking twice as long) maxes out this factor
        rt_degradation = min(
            1.0, (rolling_response_time_ms - baseline_response_time_ms) / baseline_response_time_ms
        )

    # Combine them, e.g., an average or weighted sum.
    # Here we average them, but we could also just return max() or sum().
    # Using an average means both high lesson count and high degradation are needed for 1.0 fatigue.
    fatigue = (lesson_factor + rt_degradation) / 2.0

    return min(1.0, max(0.0, fatigue))


def compute_weakness_severity(skill: SkillState | None) -> float:
    """
    Weakness severity is the complement of measured mastery: 1 - expected_mastery.

    An unobserved concept has no `SkillState` row; its severity falls back to
    the neutral prior mastery, the mean of the uninformed Beta(1, 1)
    distribution every new concept starts from (`SkillState.expected_mastery`
    with the model's default alpha=beta=1 -> 0.5), so an unmeasured concept
    reads as neither a demonstrated strength nor a demonstrated weakness.
    Source: `SkillState.expected_mastery` (learning/profiling/models.py).
    """
    mastery = skill.expected_mastery if skill is not None else SkillState().expected_mastery
    return max(0.0, min(1.0, 1.0 - mastery))


# Matches compute_session_fatigue's existing lesson-count convention above
# (lesson_factor maxes out at 20 consecutive lessons).
RECENT_ATTEMPT_VOLUME_CAP = 20


def compute_recent_session_fatigue(
    recent_attempt_count: int, recent_error_rate: float
) -> float:
    """
    Session fatigue from recent attempt VOLUME and server-verified ACCURACY
    only (M38 / G16): response-time degradation (`compute_session_fatigue`
    above) and behavioural signals are out of scope — H-016 defers
    uninstrumented behavioural-habit signals, and this endpoint has no
    session-boundary response-time baseline to degrade against.

    `volume_factor` bounds `recent_attempt_count` against
    `RECENT_ATTEMPT_VOLUME_CAP` recent attempts; `recent_error_rate` is the
    share of those attempts that were unsuccessful, already bounded to
    [0, 1] by construction. Fatigue is their average, so sustained volume
    alone or errors alone produce partial fatigue, while a long, high-error
    session reaches the [0, 1] ceiling.
    """
    volume_factor = min(1.0, max(0.0, recent_attempt_count) / RECENT_ATTEMPT_VOLUME_CAP)
    error_rate = min(1.0, max(0.0, recent_error_rate))
    return min(1.0, max(0.0, (volume_factor + error_rate) / 2.0))
