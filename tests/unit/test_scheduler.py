from datetime import datetime, timedelta

from scan64.learning.scheduling.priority import PriorityFactors, compute_session_fatigue
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule


def test_priority_ranking_formula_bounds():
    factors = PriorityFactors(
        review_due=1.0,
        weakness_severity=1.0,
        recurrence_probability=1.0,
        curriculum_relevance=1.0,
        transfer_need=1.0,
        user_interest=1.0,
        recent_overexposure=0.5,
    )

    score = factors.compute_priority(session_fatigue=0.5)
    # 6 - 0.5 - 0.5 = 5.0
    assert score == 5.0

    factors = PriorityFactors()
    assert factors.compute_priority(session_fatigue=1.0) == 0.0


def test_session_fatigue_increases_measurably():
    # Base state
    fatigue_0 = compute_session_fatigue(
        consecutive_lessons=0, baseline_response_time_ms=1000.0, rolling_response_time_ms=1000.0
    )
    assert fatigue_0 == 0.0

    # More lessons completed
    fatigue_5 = compute_session_fatigue(
        consecutive_lessons=5, baseline_response_time_ms=1000.0, rolling_response_time_ms=1000.0
    )
    assert fatigue_5 > fatigue_0

    # More lessons + degrading response time
    fatigue_5_degraded = compute_session_fatigue(
        consecutive_lessons=5, baseline_response_time_ms=1000.0, rolling_response_time_ms=1500.0
    )
    assert fatigue_5_degraded > fatigue_5

    # Max lessons + high degradation
    fatigue_max = compute_session_fatigue(
        consecutive_lessons=30, baseline_response_time_ms=1000.0, rolling_response_time_ms=3000.0
    )
    assert fatigue_max == 1.0


def test_review_schedule_due_selection():
    now = datetime(2026, 7, 14, 12, 0, 0)
    schedule = ReviewSchedule(
        player_id="player1", item_id="item1", next_review_at=now - timedelta(hours=1)
    )

    assert schedule.is_due(now)

    schedule.update(success=True, current_time=now)
    assert not schedule.is_due(now)
    assert schedule.interval_days > 1.0
    assert schedule.next_review_at > now

    schedule_not_due = ReviewSchedule(
        player_id="player1", item_id="item2", next_review_at=now + timedelta(hours=1)
    )
    assert not schedule_not_due.is_due(now)
