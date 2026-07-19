from __future__ import annotations

from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scan64.learning.profiling.habits import GameAnnotation, HabitDetector, HabitRule


def annotations_with_early_queen_moves(matches: int, total: int) -> list[GameAnnotation]:
    return [
        GameAnnotation(
            game_id=f"game-{index}",
            move_number=5 if index < matches else 12,
            piece_type="Q" if index < matches else "N",
            time_used_seconds=3.0 if index < matches else 12.0,
            opening_family="queen-gambit" if index < matches else "sicilian",
        )
        for index in range(total)
    ]


@pytest.fixture
def early_queen_rule() -> HabitRule:
    return HabitRule(
        rule_id="early-queen-move",
        description="Moves the queen early",
        max_move_number=10,
        piece_type="Q",
    )


@pytest.fixture
def synthetic_population_rates() -> Mapping[str, float]:
    return {"early-queen-move": 0.05}


def test_detector_rejects_candidate_below_minimum_support(
    early_queen_rule: HabitRule,
    synthetic_population_rates: Mapping[str, float],
) -> None:
    detector = HabitDetector([early_queen_rule], synthetic_population_rates)

    habits = detector.detect(annotations_with_early_queen_moves(matches=4, total=20))

    assert habits == []


def test_detector_rejects_candidate_at_population_rate(
    early_queen_rule: HabitRule,
) -> None:
    detector = HabitDetector([early_queen_rule], {early_queen_rule.rule_id: 0.25})

    habits = detector.detect(annotations_with_early_queen_moves(matches=5, total=20))

    assert habits == []


def test_detector_surfaces_supported_significant_habit(
    early_queen_rule: HabitRule,
    synthetic_population_rates: Mapping[str, float],
) -> None:
    detector = HabitDetector([early_queen_rule], synthetic_population_rates)

    habits = detector.detect(annotations_with_early_queen_moves(matches=5, total=20))

    assert len(habits) == 1
    habit = habits[0]
    assert habit.rule_id == early_queen_rule.rule_id
    assert habit.support_count == 5
    assert habit.opportunity_count == 20
    assert habit.population_base_rate == pytest.approx(0.05)
    assert habit.p_value < 0.05
    assert habit.supporting_game_ids == ("game-0", "game-1", "game-2", "game-3", "game-4")


@given(total=st.integers(min_value=5, max_value=60), data=st.data())
def test_detector_rejects_rate_that_does_not_exceed_synthetic_population(
    total: int,
    data: st.DataObject,
) -> None:
    matches = data.draw(st.integers(min_value=5, max_value=total))
    rule = HabitRule(
        rule_id="early-queen-move",
        description="Moves the queen early",
        max_move_number=10,
        piece_type="Q",
    )
    detector = HabitDetector([rule], {rule.rule_id: matches / total})

    habits = detector.detect(annotations_with_early_queen_moves(matches=matches, total=total))

    assert habits == []
