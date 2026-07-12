from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from scan64.learning.profiling.models import SkillState


@given(
    alpha=st.floats(min_value=0.1, max_value=100.0),
    beta=st.floats(min_value=0.1, max_value=100.0)
)
def test_success_increases_mastery(alpha, beta):
    state = SkillState(player_id="test", concept_code="test", alpha=alpha, beta=beta)
    initial_mastery = state.expected_mastery

    state.apply_observation(success=True)

    assert state.expected_mastery > initial_mastery

@given(
    alpha=st.floats(min_value=0.1, max_value=100.0),
    beta=st.floats(min_value=0.1, max_value=100.0)
)
def test_failure_decreases_mastery(alpha, beta):
    state = SkillState(player_id="test", concept_code="test", alpha=alpha, beta=beta)
    initial_mastery = state.expected_mastery

    state.apply_observation(success=False)

    assert state.expected_mastery < initial_mastery

@given(
    dt_days=st.floats(min_value=0.1, max_value=3650.0),
    alpha_evidence=st.floats(min_value=0.0, max_value=50.0),
    beta_evidence=st.floats(min_value=0.0, max_value=50.0)
)
def test_decay_shrinks_evidence(dt_days, alpha_evidence, beta_evidence):
    # Set up state with some prior and accumulated evidence
    prior_alpha, prior_beta = 1.0, 1.0
    initial_alpha = prior_alpha + alpha_evidence
    initial_beta = prior_beta + beta_evidence

    now = datetime(2025, 1, 1, 12, 0, 0)
    later = now + timedelta(days=dt_days)

    state = SkillState(
        player_id="test",
        concept_code="test",
        alpha=initial_alpha,
        beta=initial_beta,
        prior_alpha=prior_alpha,
        prior_beta=prior_beta,
        last_updated=now
    )

    # We invoke _decay directly to avoid adding a new observation for pure decay test
    state._decay(later)

    # Evidence should be strictly smaller after decay if there was any evidence
    new_evidence_alpha = state.alpha - state.prior_alpha
    new_evidence_beta = state.beta - state.prior_beta

    if alpha_evidence > 0.01:
        assert new_evidence_alpha < alpha_evidence
    if beta_evidence > 0.01:
        assert new_evidence_beta < beta_evidence

    # Values should never drop below priors
    assert state.alpha >= prior_alpha - 1e-7
    assert state.beta >= prior_beta - 1e-7

@given(
    success=st.booleans(),
    hint_assisted=st.booleans()
)
def test_uncertainty_shrinks_with_evidence(success, hint_assisted):
    state = SkillState(player_id="test", concept_code="test", alpha=1.0, beta=1.0)
    initial_uncertainty = state.uncertainty

    state.apply_observation(success=success, hint_assisted=hint_assisted)

    assert state.uncertainty < initial_uncertainty
