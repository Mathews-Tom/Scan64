from __future__ import annotations

import pytest

from scan64.learning.evaluation.controlled_study import (
    STUDY_CONDITIONS,
    ControlledStudyError,
    StudyCondition,
    assign_conditions,
)


def test_four_conditions_are_defined_per_source_section_30_2() -> None:
    assert set(STUDY_CONDITIONS) == {
        StudyCondition.CONVENTIONAL_ENGINE_REVIEW,
        StudyCondition.GENERIC_LLM_EXPLANATION,
        StudyCondition.EXACT_MISTAKE_REPLAY,
        StudyCondition.PERSONALIZED_DIAGNOSIS_TRANSFER,
    }
    assert len(STUDY_CONDITIONS) == 4


def test_every_participant_receives_exactly_one_assignment() -> None:
    participant_ids = [f"p{i}" for i in range(37)]

    report = assign_conditions(participant_ids, seed=1)

    assert len(report.assignments) == len(participant_ids)
    assert {a.participant_id for a in report.assignments} == set(participant_ids)


def test_assignment_is_balanced_over_a_simulated_recruitment_run() -> None:
    """Simulated recruitment of 400 participants stays balanced across conditions."""
    participant_ids = [f"participant-{i}" for i in range(400)]

    report = assign_conditions(participant_ids, seed=42)

    assert report.is_balanced
    counts = report.condition_counts
    assert sum(counts.values()) == len(participant_ids)
    assert max(counts.values()) - min(counts.values()) <= 1


@pytest.mark.parametrize("recruitment_size", [1, 2, 3, 4, 5, 7, 8, 40, 401, 1000])
def test_assignment_stays_balanced_at_every_recruitment_size(recruitment_size: int) -> None:
    """Balance must hold whether recruitment lands mid-block or on a block boundary."""
    participant_ids = [f"p{i}" for i in range(recruitment_size)]

    report = assign_conditions(participant_ids, seed=7)

    assert report.is_balanced


def test_assignment_is_random_not_fixed_ordering() -> None:
    """Different seeds over full blocks must be able to produce different orderings.

    Guards against a regression to a deterministic round-robin (which would not
    be genuine random assignment per source §23.5).
    """
    participant_ids = [f"p{i}" for i in range(4)]

    orderings = {
        tuple(a.condition for a in assign_conditions(participant_ids, seed=seed).assignments)
        for seed in range(20)
    }

    assert len(orderings) > 1


def test_assignment_is_reproducible_for_a_fixed_seed() -> None:
    participant_ids = [f"p{i}" for i in range(23)]

    first = assign_conditions(participant_ids, seed=99)
    second = assign_conditions(participant_ids, seed=99)

    assert first.assignments == second.assignments


def test_duplicate_participant_ids_are_rejected() -> None:
    with pytest.raises(ControlledStudyError, match="unique"):
        assign_conditions(["p1", "p2", "p1"], seed=1)


def test_empty_participant_id_is_rejected() -> None:
    with pytest.raises(ControlledStudyError, match="empty"):
        assign_conditions(["p1", "   ", "p3"], seed=1)


def test_empty_recruitment_batch_produces_no_assignments() -> None:
    report = assign_conditions([], seed=1)

    assert report.assignments == ()
    assert report.is_balanced
