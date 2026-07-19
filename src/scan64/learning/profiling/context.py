from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from scan64.learning.profiling.models import SkillState

DEFAULT_MINIMUM_CONTEXT_OPPORTUNITIES = 10


@dataclass(frozen=True, slots=True)
class SkillContext:
    """Annotation-derived dimensions used to segment a skill observation."""

    opening_or_pawn_structure: str
    phase: str
    colour: str
    time_control: str
    clock_pressure: str
    source: str

    def __post_init__(self) -> None:
        for value in (
            self.opening_or_pawn_structure,
            self.phase,
            self.colour,
            self.time_control,
            self.clock_pressure,
            self.source,
        ):
            if not value.strip():
                raise ValueError("skill context dimensions must be non-empty")


@dataclass(frozen=True, slots=True)
class ContextObservation:
    """One opportunity to demonstrate a skill in a fully specified context."""

    context: SkillContext
    success: bool


@dataclass(frozen=True, slots=True)
class ContextEstimate:
    """A context-cell mastery estimate before display-evidence gating."""

    context: SkillContext
    mastery: float
    opportunities: int
    global_mastery: float


@dataclass(frozen=True, slots=True)
class ContextMasteryClaim:
    """A display-safe mastery claim with its evidence source recorded."""

    context: SkillContext
    mastery: float
    opportunities: int
    is_context_conditioned: bool


class ContextConditionedSkillModel:
    """Shrinks sparse context-cell mastery estimates toward global skill mastery."""

    def __init__(
        self,
        global_skill_state: SkillState,
        observations: Iterable[ContextObservation],
        *,
        prior_strength: float = 10.0,
    ) -> None:
        if prior_strength <= 0.0:
            raise ValueError("prior_strength must be positive")

        self._global_mastery = global_skill_state.expected_mastery
        self._prior_strength = prior_strength
        self._counts: dict[SkillContext, tuple[int, int]] = {}
        for observation in observations:
            opportunities, successes = self._counts.get(observation.context, (0, 0))
            self._counts[observation.context] = (
                opportunities + 1,
                successes + int(observation.success),
            )

    @property
    def global_mastery(self) -> float:
        return self._global_mastery

    def estimate(self, context: SkillContext) -> ContextEstimate:
        """Return the partial-pooled mastery estimate for one context cell."""
        opportunities, successes = self._counts.get(context, (0, 0))
        mastery = (successes + self._prior_strength * self._global_mastery) / (
            opportunities + self._prior_strength
        )
        return ContextEstimate(
            context=context,
            mastery=mastery,
            opportunities=opportunities,
            global_mastery=self._global_mastery,
        )

    def surfaced_claim(
        self,
        context: SkillContext,
        *,
        minimum_opportunities: int = DEFAULT_MINIMUM_CONTEXT_OPPORTUNITIES,
    ) -> ContextMasteryClaim:
        """Return a display-safe claim, falling back to global mastery when needed."""
        if minimum_opportunities <= 0:
            raise ValueError("minimum_opportunities must be positive")

        estimate = self.estimate(context)
        is_context_conditioned = estimate.opportunities >= minimum_opportunities
        mastery = estimate.mastery if is_context_conditioned else self._global_mastery
        return ContextMasteryClaim(
            context=context,
            mastery=mastery,
            opportunities=estimate.opportunities,
            is_context_conditioned=is_context_conditioned,
        )
