import math

from pydantic import BaseModel

from scan64.learning.diagnosis.models import DiagnosisCandidate


class RankingFactors(BaseModel):
    severity: float = 1.0
    teachability: float = 1.0
    recurrence: float = 1.0
    confidence: float = 1.0
    transfer_value: float = 1.0
    readiness: float = 1.0
    redundancy: float = 0.0
    cognitive_overload: float = 0.0


class RankedOpportunity(BaseModel):
    candidate: DiagnosisCandidate
    score: float
    factors: RankingFactors


class OpportunityRanker:
    def __init__(self, epsilon: float = 1e-4, overcoaching_cap: int = 3):
        self.epsilon = epsilon
        self.overcoaching_cap = overcoaching_cap

    def _safe_log(self, value: float) -> float:
        return math.log(max(value, self.epsilon))

    def calculate_score(self, factors: RankingFactors) -> float:
        """
        Calculates score using log-additive form:
        score = sum(log(factor)) - redundancy - cognitive_overload
        """
        score = 0.0
        score += self._safe_log(factors.severity)
        score += self._safe_log(factors.teachability)
        score += self._safe_log(factors.recurrence)
        score += self._safe_log(factors.confidence)
        score += self._safe_log(factors.transfer_value)
        score += self._safe_log(factors.readiness)

        # Penalties are subtracted
        score -= factors.redundancy
        score -= factors.cognitive_overload

        return score

    def rank(
        self,
        candidates: list[tuple[DiagnosisCandidate, RankingFactors]],
    ) -> list[RankedOpportunity]:
        ranked = []
        for candidate, factors in candidates:
            score = self.calculate_score(factors)
            ranked.append(RankedOpportunity(candidate=candidate, score=score, factors=factors))

        # Sort descending by score
        ranked.sort(key=lambda x: x.score, reverse=True)

        # Apply overcoaching cap
        return ranked[: self.overcoaching_cap]
