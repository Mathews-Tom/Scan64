from datetime import datetime, timedelta

from scan64.learning.profiling.models import SkillState
from scan64.learning.profiling.priors import get_prior_for_rating


def test_initial_state():
    state = SkillState(player_id="user1", concept_code="tactics.pin")
    assert state.alpha == 1.0
    assert state.beta == 1.0
    assert state.expected_mastery == 0.5
    assert state.uncertainty > 0.0


def test_independent_success():
    state = SkillState(player_id="user1", concept_code="tactics.pin")
    state.apply_observation(success=True, hint_assisted=False)
    assert state.alpha == 2.0
    assert state.beta == 1.0
    assert state.expected_mastery == 2.0 / 3.0


def test_hint_assisted_success():
    state = SkillState(player_id="user1", concept_code="tactics.pin")
    state.apply_observation(success=True, hint_assisted=True)
    assert state.alpha == 1.5
    assert state.beta == 1.0
    assert state.expected_mastery == 1.5 / 2.5
    assert state.expected_mastery < (2.0 / 3.0)  # less than independent success


def test_failure():
    state = SkillState(player_id="user1", concept_code="tactics.pin")
    state.apply_observation(success=False)
    assert state.alpha == 1.0
    assert state.beta == 2.0
    assert state.expected_mastery == 1.0 / 3.0


def test_uncertainty_decreases_with_evidence():
    state = SkillState(player_id="user1", concept_code="tactics.pin")

    # After 5 successes
    for _ in range(5):
        state.apply_observation(success=True)

    uncertainty_5 = state.uncertainty

    # After 50 successes
    for _ in range(45):
        state.apply_observation(success=True)

    uncertainty_50 = state.uncertainty

    assert uncertainty_50 < uncertainty_5


def test_decay_over_time():
    now = datetime(2025, 1, 1, 12, 0, 0)
    state = SkillState(player_id="user1", concept_code="tactics.pin")

    # Apply some initial successes to build evidence
    state.apply_observation(success=True, timestamp=now)
    state.apply_observation(success=True, timestamp=now)

    evidence_alpha = state.alpha

    # Apply an observation exactly tau (90 days) later
    later = now + timedelta(days=90)
    state.apply_observation(success=False, timestamp=later)

    # Since decay is applied BEFORE the new observation is added,
    # at exactly tau days, the previous evidence should be scaled by exp(-1) ≈ 0.3678
    import math

    expected_decayed_alpha = 1.0 + (evidence_alpha - 1.0) * math.exp(-1)

    # After decay, the new observation (failure) is added
    assert abs(state.alpha - expected_decayed_alpha) < 1e-5

    # Beta prior is 1.0, and decay should have applied to evidence (0), so it stays 1.0,
    # then failure adds 1.0 to beta
    assert abs(state.beta - 2.0) < 1e-5


def test_priors_table():
    prior_low = get_prior_for_rating(800)
    assert prior_low == (1.0, 4.0)

    prior_high = get_prior_for_rating(2200)
    assert prior_high == (4.0, 1.0)

    prior_mid = get_prior_for_rating(1200)
    assert prior_mid == (2.0, 3.0)
