from scan64.learning.profiling.models import SkillState

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
