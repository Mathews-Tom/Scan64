from __future__ import annotations

import pytest

from scan64.learning.profiling.context import (
    ContextConditionedSkillModel,
    ContextObservation,
    SkillContext,
)
from scan64.learning.profiling.habits import GameAnnotation
from scan64.learning.profiling.models import SkillState


@pytest.fixture
def synthetic_play_history() -> tuple[list[ContextObservation], list[GameAnnotation]]:
    context = SkillContext(
        opening_or_pawn_structure="queen-gambit",
        phase="middlegame",
        colour="white",
        time_control="rapid",
        clock_pressure="high",
        source="game",
    )
    observations = [
        ContextObservation(context=context, success=index % 2 == 0) for index in range(10)
    ]
    annotations = [
        GameAnnotation(
            game_id=f"game-{index}",
            move_number=5 if index < 5 else 12,
            piece_type="Q" if index < 5 else "N",
            time_used_seconds=3.0 if index < 5 else 12.0,
            opening_family="queen-gambit" if index < 5 else "sicilian",
        )
        for index in range(20)
    ]
    return observations, annotations


def test_play_history_surfaces_context_claim_after_minimum_evidence(
    synthetic_play_history: tuple[list[ContextObservation], list[GameAnnotation]],
) -> None:
    observations, _ = synthetic_play_history
    global_skill = SkillState(
        player_id="player-1",
        concept_code="tactics.fork",
        alpha=3.0,
        beta=1.0,
    )
    model = ContextConditionedSkillModel(global_skill, observations)

    claim = model.surfaced_claim(observations[0].context)

    assert claim.is_context_conditioned is True
    assert claim.opportunities == 10
    assert claim.mastery == pytest.approx(0.625)
