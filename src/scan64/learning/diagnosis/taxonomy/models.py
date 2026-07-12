from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SkillTier(StrEnum):
    EVENT = "event-tier"
    PROCESS = "process-tier"


class SkillDefinition(BaseModel):
    """
    Defines a diagnostic taxonomy entry for Scan64.
    Matches the governance requirements in §8.10 of the system design.
    """

    model_config = ConfigDict(frozen=True)

    skill_id: str = Field(..., description="Unique identifier for the skill/diagnosis code")
    name: str = Field(..., description="Human-readable name")
    parent_id: str = Field(
        ..., description="Parent code ID terminating at a top-level category (§8.1-§8.9)"
    )
    tier: SkillTier = Field(..., description="Event-tier or process-tier tag")

    detection_requirements: str = Field(
        ..., description="How this skill is detected in a game or exercise"
    )
    positive_examples: list[str] = Field(
        default_factory=list, description="FENs or game states demonstrating the skill"
    )
    counterexamples: list[str] = Field(
        default_factory=list,
        description=(
            "FENs or game states demonstrating confusable situations that are NOT this skill"
        ),
    )
    confidence_calculation: str = Field(
        ..., description="How confidence in the diagnosis is calculated"
    )
    compatible_exercise_templates: list[str] = Field(
        default_factory=list, description="IDs of exercise templates compatible with this skill"
    )
    incompatible_diagnoses: list[str] = Field(
        default_factory=list, description="Other skill IDs that cannot be simultaneously diagnosed"
    )
    minimum_engine_evidence: str = Field(
        ..., description="Engine requirements to validate this diagnosis"
    )
