from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from random import Random


class ControlledStudyError(ValueError):
    """Raised when controlled-study random assignment cannot proceed safely."""


class StudyCondition(StrEnum):
    """The 4 source §30.2 controlled multi-arm study conditions.

    Learning-gain comparisons require random assignment after recruitment, not
    self-selected condition choice (§23.5): self-selection confounds motivation
    with treatment. This module assigns; it does not recruit or run the study.
    """

    CONVENTIONAL_ENGINE_REVIEW = "conventional_engine_review"
    GENERIC_LLM_EXPLANATION = "generic_llm_explanation"
    EXACT_MISTAKE_REPLAY = "exact_mistake_replay"
    PERSONALIZED_DIAGNOSIS_TRANSFER = "personalized_diagnosis_transfer"


STUDY_CONDITIONS: tuple[StudyCondition, ...] = tuple(StudyCondition)


@dataclass(frozen=True)
class ConditionAssignment:
    """One participant's random condition assignment."""

    participant_id: str
    condition: StudyCondition
    block_index: int


@dataclass(frozen=True)
class RandomAssignmentReport:
    """The outcome of assigning a batch of recruited participants to conditions."""

    assignments: tuple[ConditionAssignment, ...]

    @property
    def condition_counts(self) -> dict[StudyCondition, int]:
        counts = Counter(assignment.condition for assignment in self.assignments)
        return {condition: counts.get(condition, 0) for condition in STUDY_CONDITIONS}

    @property
    def is_balanced(self) -> bool:
        """True when no condition has more than one extra participant than any other.

        Permuted-block randomization (below) guarantees this: every complete block
        of `len(STUDY_CONDITIONS)` participants assigns each condition exactly
        once, so imbalance can only come from one trailing partial block.
        """
        counts = self.condition_counts.values()
        if not counts:
            return True
        return max(counts) - min(counts) <= 1


def assign_conditions(
    participant_ids: Sequence[str],
    *,
    seed: int | None = None,
) -> RandomAssignmentReport:
    """Randomly assign recruited participants to the 4 §30.2 study conditions.

    Uses permuted-block randomization: participants are grouped into blocks of
    `len(STUDY_CONDITIONS)` in recruitment order, and within each block every
    condition is assigned exactly once via a random shuffle. This keeps
    assignment counts balanced across conditions at any point during a rolling
    recruitment, unlike unconstrained per-participant coin-flip assignment,
    while still being random (not self-selected, per §23.5) within each block.

    `seed` makes assignment reproducible for tests and audits; omit it for a
    non-deterministic (`random`-seeded) real recruitment run.
    """
    if len(participant_ids) != len(set(participant_ids)):
        raise ControlledStudyError("participant_ids must be unique")
    for participant_id in participant_ids:
        if not participant_id.strip():
            raise ControlledStudyError("participant_ids must not contain empty identifiers")

    rng = Random(seed)
    block_size = len(STUDY_CONDITIONS)
    assignments: list[ConditionAssignment] = []

    for block_start in range(0, len(participant_ids), block_size):
        block_participants = participant_ids[block_start : block_start + block_size]
        block_index = block_start // block_size

        block_conditions = list(STUDY_CONDITIONS)
        rng.shuffle(block_conditions)

        for participant_id, condition in zip(block_participants, block_conditions, strict=False):
            assignments.append(
                ConditionAssignment(
                    participant_id=participant_id,
                    condition=condition,
                    block_index=block_index,
                )
            )

    return RandomAssignmentReport(assignments=tuple(assignments))
