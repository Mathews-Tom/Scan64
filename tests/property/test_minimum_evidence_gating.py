import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from scan64.learning.profiling.context import (
    DEFAULT_MINIMUM_CONTEXT_OPPORTUNITIES,
    ContextConditionedSkillModel,
    ContextObservation,
    SkillContext,
)
from scan64.learning.profiling.models import SkillState

CONTEXT = SkillContext(
    opening_or_pawn_structure="queen-gambit",
    phase="middlegame",
    colour="white",
    time_control="rapid",
    clock_pressure="low",
    source="game",
)


@example(outcomes=[True] * (DEFAULT_MINIMUM_CONTEXT_OPPORTUNITIES - 1))
@example(outcomes=[True] * DEFAULT_MINIMUM_CONTEXT_OPPORTUNITIES)
@given(outcomes=st.lists(st.booleans(), max_size=25))
def test_minimum_evidence_gate_surfaces_only_supported_context_claims(outcomes):
    global_skill = SkillState(
        player_id="player-1",
        concept_code="tactics.fork",
        alpha=3.0,
        beta=1.0,
    )
    model = ContextConditionedSkillModel(
        global_skill,
        [ContextObservation(context=CONTEXT, success=outcome) for outcome in outcomes],
    )

    claim = model.surfaced_claim(CONTEXT)

    assert claim.opportunities == len(outcomes)
    assert claim.is_context_conditioned == (
        len(outcomes) >= DEFAULT_MINIMUM_CONTEXT_OPPORTUNITIES
    )
    if len(outcomes) < DEFAULT_MINIMUM_CONTEXT_OPPORTUNITIES:
        assert claim.mastery == pytest.approx(global_skill.expected_mastery)
    else:
        assert claim.mastery == pytest.approx(model.estimate(CONTEXT).mastery)
