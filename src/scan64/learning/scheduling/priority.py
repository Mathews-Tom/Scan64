from dataclasses import dataclass


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
    - Degradation fatigue based on how much slower the rolling response time is compared to baseline.
    """
    if consecutive_lessons < 0:
        consecutive_lessons = 0

    # Linearly increase up to 20 lessons
    lesson_factor = min(1.0, consecutive_lessons / 20.0)

    rt_degradation = 0.0
    if baseline_response_time_ms > 0 and rolling_response_time_ms > baseline_response_time_ms:
        # 100% degradation (taking twice as long) maxes out this factor
        rt_degradation = min(1.0, (rolling_response_time_ms - baseline_response_time_ms) / baseline_response_time_ms)

    # Combine them, e.g., an average or weighted sum. 
    # Here we average them, but we could also just return max() or sum().
    # Using an average means both high lesson count and high degradation are needed for 1.0 fatigue.
    fatigue = (lesson_factor + rt_degradation) / 2.0
    
    return min(1.0, max(0.0, fatigue))
