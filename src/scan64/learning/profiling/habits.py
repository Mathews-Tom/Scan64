from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

DEFAULT_MINIMUM_HABIT_SUPPORT = 5
DEFAULT_SIGNIFICANCE_LEVEL = 0.05


@dataclass(frozen=True, slots=True)
class GameAnnotation:
    """A directly-computable behavioural observation from a game annotation."""

    game_id: str
    move_number: int
    piece_type: str
    time_used_seconds: float
    opening_family: str

    def __post_init__(self) -> None:
        if not self.game_id.strip():
            raise ValueError("game_id must be non-empty")
        if self.move_number <= 0:
            raise ValueError("move_number must be positive")
        if not self.piece_type.strip():
            raise ValueError("piece_type must be non-empty")
        if self.time_used_seconds < 0.0:
            raise ValueError("time_used_seconds must be non-negative")
        if not self.opening_family.strip():
            raise ValueError("opening_family must be non-empty")


@dataclass(frozen=True, slots=True)
class HabitRule:
    """A v1 habit predicate over directly-computable annotation fields only."""

    rule_id: str
    description: str
    max_move_number: int | None = None
    piece_type: str | None = None
    max_time_used_seconds: float | None = None
    opening_family: str | None = None

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("rule_id must be non-empty")
        if not self.description.strip():
            raise ValueError("description must be non-empty")
        if self.max_move_number is not None and self.max_move_number <= 0:
            raise ValueError("max_move_number must be positive")
        if self.piece_type is not None and not self.piece_type.strip():
            raise ValueError("piece_type must be non-empty when supplied")
        if self.max_time_used_seconds is not None and self.max_time_used_seconds < 0.0:
            raise ValueError("max_time_used_seconds must be non-negative")
        if self.opening_family is not None and not self.opening_family.strip():
            raise ValueError("opening_family must be non-empty when supplied")
        if (
            self.max_move_number is None
            and self.piece_type is None
            and self.max_time_used_seconds is None
            and self.opening_family is None
        ):
            raise ValueError("habit rules must contain at least one direct predicate")

    def matches(self, annotation: GameAnnotation) -> bool:
        """Return whether an annotation satisfies every configured predicate."""
        return (
            (self.max_move_number is None or annotation.move_number <= self.max_move_number)
            and (self.piece_type is None or annotation.piece_type == self.piece_type)
            and (
                self.max_time_used_seconds is None
                or annotation.time_used_seconds <= self.max_time_used_seconds
            )
            and (self.opening_family is None or annotation.opening_family == self.opening_family)
        )


@dataclass(frozen=True, slots=True)
class Habit:
    """A surfaced habit with inspectable supporting annotations and statistical evidence."""

    rule_id: str
    description: str
    support_count: int
    opportunity_count: int
    observed_rate: float
    population_base_rate: float
    p_value: float
    supporting_game_ids: tuple[str, ...]


def binomial_survival_probability(
    opportunities: int,
    successes: int,
    population_base_rate: float,
) -> float:
    """Return P(X >= successes) for X ~ Binomial(opportunities, population_base_rate)."""
    if opportunities < 0:
        raise ValueError("opportunities must be non-negative")
    if successes < 0 or successes > opportunities:
        raise ValueError("successes must be between zero and opportunities")
    if not 0.0 <= population_base_rate <= 1.0:
        raise ValueError("population_base_rate must be between zero and one")
    if successes == 0:
        return 1.0
    if population_base_rate == 0.0:
        return 0.0
    if population_base_rate == 1.0:
        return 1.0

    log_terms = [
        (
            math.lgamma(opportunities + 1)
            - math.lgamma(count + 1)
            - math.lgamma(opportunities - count + 1)
            + count * math.log(population_base_rate)
            + (opportunities - count) * math.log1p(-population_base_rate)
        )
        for count in range(successes, opportunities + 1)
    ]
    maximum_log_term = max(log_terms)
    scaled_terms = sum(math.exp(term - maximum_log_term) for term in log_terms)
    return min(1.0, math.exp(maximum_log_term) * scaled_terms)


class HabitDetector:
    """Surface only direct-predicate habits with adequate support and significance."""

    def __init__(
        self,
        rules: Iterable[HabitRule],
        population_base_rates: Mapping[str, float],
        *,
        minimum_support: int = DEFAULT_MINIMUM_HABIT_SUPPORT,
        significance_level: float = DEFAULT_SIGNIFICANCE_LEVEL,
    ) -> None:
        if minimum_support <= 0:
            raise ValueError("minimum_support must be positive")
        if not 0.0 < significance_level < 1.0:
            raise ValueError("significance_level must be between zero and one")

        self._rules = tuple(rules)
        rule_ids = {rule.rule_id for rule in self._rules}
        if len(rule_ids) != len(self._rules):
            raise ValueError("habit rule identifiers must be unique")
        missing_base_rates = rule_ids.difference(population_base_rates)
        if missing_base_rates:
            missing_rule_ids = sorted(missing_base_rates)
            raise ValueError(f"missing population base rates for rules: {missing_rule_ids}")
        for rule_id in rule_ids:
            base_rate = population_base_rates[rule_id]
            if not 0.0 <= base_rate <= 1.0:
                raise ValueError("population base rates must be between zero and one")

        self._population_base_rates = dict(population_base_rates)
        self._minimum_support = minimum_support
        self._significance_level = significance_level

    def detect(self, annotations: Iterable[GameAnnotation]) -> list[Habit]:
        """Return statistically supported habits found in the supplied game annotations."""
        observations = tuple(annotations)
        opportunity_count = len(observations)
        if opportunity_count == 0:
            return []

        habits: list[Habit] = []
        for rule in self._rules:
            supporting_annotations = tuple(
                annotation for annotation in observations if rule.matches(annotation)
            )
            support_count = len(supporting_annotations)
            if support_count < self._minimum_support:
                continue

            population_base_rate = self._population_base_rates[rule.rule_id]
            p_value = binomial_survival_probability(
                opportunity_count,
                support_count,
                population_base_rate,
            )
            if p_value >= self._significance_level:
                continue

            habits.append(
                Habit(
                    rule_id=rule.rule_id,
                    description=rule.description,
                    support_count=support_count,
                    opportunity_count=opportunity_count,
                    observed_rate=support_count / opportunity_count,
                    population_base_rate=population_base_rate,
                    p_value=p_value,
                    supporting_game_ids=tuple(
                        dict.fromkeys(annotation.game_id for annotation in supporting_annotations)
                    ),
                )
            )
        return habits
