from typing import Any

from pydantic import BaseModel, Field


class LearningOpportunity(BaseModel):
    opportunity_id: str
    position_id: str
    player_id: str
    game_id: str | None = None
    played_move: str
    engine_eval_before: float
    engine_eval_after: float


class PlayerContext(BaseModel):
    player_id: str
    history_summary: dict[str, Any] = Field(default_factory=dict)


class DiagnosisCandidate(BaseModel):
    skill_id: str
    confidence: float
    evidence_ids: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)
