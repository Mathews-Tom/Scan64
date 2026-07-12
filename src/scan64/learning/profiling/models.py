from datetime import datetime
from sqlmodel import Field, SQLModel


class SkillState(SQLModel, table=False):
    """
    Represents a player's mastery of a specific skill concept.
    Uses a Beta distribution (alpha, beta) for Bayesian knowledge tracing.
    """
    player_id: str = Field(primary_key=True)
    concept_code: str = Field(primary_key=True)
    
    # Beta distribution parameters
    alpha: float = Field(default=1.0)
    beta: float = Field(default=1.0)
    
    def apply_observation(self, success: bool, hint_assisted: bool = False) -> None:
        """
        Update the mastery distribution based on an observation.
        An independent success increases alpha by 1.0.
        A hint-assisted success increases alpha by a smaller increment.
        A failure increases beta by 1.0.
        """
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
        return (self.alpha * self.beta) / ((total ** 2) * (total + 1.0))
