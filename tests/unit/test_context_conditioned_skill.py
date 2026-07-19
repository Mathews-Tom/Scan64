import pytest

from scan64.learning.profiling.context import (
    ContextConditionedSkillModel,
    ContextObservation,
    SkillContext,
)
from scan64.learning.profiling.models import SkillState


def test_model_shrinks_sparse_context_toward_global_mastery():
    global_skill = SkillState(
        player_id="player-1",
        concept_code="tactics.fork",
        alpha=8.0,
        beta=2.0,
    )
    context = SkillContext(
        opening_or_pawn_structure="queen-gambit",
        phase="middlegame",
        colour="white",
        time_control="rapid",
        clock_pressure="low",
        source="game",
    )
    model = ContextConditionedSkillModel(
        global_skill,
        [
            ContextObservation(context=context, success=False),
            ContextObservation(context=context, success=False),
        ],
    )

    estimate = model.estimate(context)

    assert estimate.opportunities == 2
    assert estimate.global_mastery == pytest.approx(0.8)
    assert estimate.mastery == pytest.approx(8.0 / 12.0)


def test_model_uses_global_mastery_for_unobserved_context():
    global_skill = SkillState(
        player_id="player-1",
        concept_code="tactics.fork",
        alpha=3.0,
        beta=1.0,
    )
    context = SkillContext(
        opening_or_pawn_structure="caro-kann",
        phase="endgame",
        colour="black",
        time_control="blitz",
        clock_pressure="high",
        source="exercise",
    )

    estimate = ContextConditionedSkillModel(global_skill, []).estimate(context)

    assert estimate.opportunities == 0
    assert estimate.mastery == pytest.approx(global_skill.expected_mastery)


def test_model_maintains_separate_cells_for_each_context_dimension():
    global_skill = SkillState(player_id="player-1", concept_code="tactics.fork")
    base = SkillContext(
        opening_or_pawn_structure="sicilian",
        phase="middlegame",
        colour="white",
        time_control="rapid",
        clock_pressure="low",
        source="game",
    )
    different_pressure = SkillContext(
        opening_or_pawn_structure="sicilian",
        phase="middlegame",
        colour="white",
        time_control="rapid",
        clock_pressure="high",
        source="game",
    )
    model = ContextConditionedSkillModel(
        global_skill,
        [ContextObservation(context=base, success=True)],
    )

    assert model.estimate(base).opportunities == 1
    assert model.estimate(different_pressure).opportunities == 0
