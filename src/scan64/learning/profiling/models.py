import math
from datetime import datetime

from sqlmodel import Field, SQLModel


class SkillState(SQLModel, table=True):
    """
    Represents a player's mastery of a specific skill concept.
    Uses a Beta distribution (alpha, beta) for Bayesian knowledge tracing.
    """

    player_id: str = Field(primary_key=True)
    concept_code: str = Field(primary_key=True)

    # Beta distribution parameters
    alpha: float = Field(default=1.0)
    beta: float = Field(default=1.0)

    # Base priors to decay towards
    prior_alpha: float = Field(default=1.0)
    prior_beta: float = Field(default=1.0)

    last_updated: datetime | None = Field(default=None)

    def _decay(self, current_time: datetime, tau_days: float = 90.0) -> None:
        if self.last_updated is None:
            return

        dt = (current_time - self.last_updated).total_seconds() / (24 * 3600)
        if dt <= 0:
            return

        weight = math.exp(-dt / tau_days)

        # Decay evidence towards the prior
        evidence_alpha = max(0.0, self.alpha - self.prior_alpha)
        evidence_beta = max(0.0, self.beta - self.prior_beta)

        self.alpha = self.prior_alpha + weight * evidence_alpha
        self.beta = self.prior_beta + weight * evidence_beta

    def apply_observation(
        self, success: bool, hint_assisted: bool = False, timestamp: datetime | None = None
    ) -> None:
        """
        Update the mastery distribution based on an observation.
        Applies time decay if a timestamp is provided and last_updated is set.
        An independent success increases alpha by 1.0.
        A hint-assisted success increases alpha by 0.5.
        A failure increases beta by 1.0.
        """
        if timestamp:
            self._decay(timestamp)
            self.last_updated = timestamp

        if success:
            if hint_assisted:
                self.alpha += 0.5
            else:
                self.alpha += 1.0
        else:
            self.beta += 1.0

    @property
    def expected_mastery(self) -> float:
        """Mean of the Beta distribution: alpha / (alpha + beta)"""
        return self.alpha / (self.alpha + self.beta)

    @property
    def uncertainty(self) -> float:
        """Variance of the Beta distribution"""
        total = self.alpha + self.beta
        return (self.alpha * self.beta) / ((total**2) * (total + 1.0))
