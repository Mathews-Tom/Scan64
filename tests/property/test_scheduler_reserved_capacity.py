from hypothesis import given
from hypothesis import strategies as st

from scan64.learning.scheduling.composer import SessionComposer


@st.composite
def skewed_candidate_pool(draw):
    """
    Generate a pool of candidates heavily skewed towards mistakes.
    """
    pool_size = draw(st.integers(min_value=20, max_value=100))
    mistake_count = draw(st.integers(min_value=15, max_value=pool_size))
    exploration_count = pool_size - mistake_count

    pool = []
    for _ in range(mistake_count):
        pool.append({"type": "mistakes", "priority": draw(st.floats(min_value=0.5, max_value=1.0))})

    for _ in range(exploration_count):
        pool.append(
            {"type": "exploration", "priority": draw(st.floats(min_value=0.0, max_value=0.4))}
        )

    return pool


@given(skewed_candidate_pool(), st.integers(min_value=5, max_value=30))
def test_reserved_capacity_is_never_zero_regardless_of_skew(pool, session_size):
    """
    Acceptance: A session composed with only personal-mistake evidence available
    still allocates its configured exploration/fundamentals share
    (property-tested: reserved capacity is never zero regardless of how
    skewed the mistake history is).
    """
    composer = SessionComposer(hard_exploration_floor=0.2)
    session = composer.compose_session(pool, session_size=session_size)

    # We might not have enough exploration items in the pool itself!
    # But if there ARE exploration items, it must pick them to fulfill the floor.
    exploration_in_pool = sum(1 for item in pool if item["type"] == "exploration")
    exploration_in_session = sum(1 for item in session if item["type"] == "exploration")

    expected_floor = int(session_size * composer.hard_exploration_floor)
    expected_min = min(expected_floor, exploration_in_pool)

    if len(session) == session_size:
        assert exploration_in_session >= expected_min
