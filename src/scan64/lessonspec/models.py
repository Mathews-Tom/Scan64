from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DomainEventEnvelope(BaseModel):
    """Standard envelope for internal domain events."""
    event_id: str
    occurred_at: datetime
    schema_version: str
    correlation_id: str
    causation_id: str
    event_type: str
    payload: dict[str, Any]

class Source(BaseModel):
    kind: Literal["player_game", "custom", "external"]
    fen: str

class Diagnosis(BaseModel):
    primary: str
    secondary: list[str] = Field(default_factory=list)
    confidence: float
    evidence_refs: list[str] = Field(default_factory=list)

class Objective(BaseModel):
    type: str
    instruction: str

class AcceptedMove(BaseModel):
    san: str
    lan: str | None = None
    reason: str | None = None

class Interaction(BaseModel):
    input: str
    maximum_attempts: int
    accepted_moves: list[AcceptedMove] = Field(default_factory=list)

class Hint(BaseModel):
    level: int
    kind: str
    text: str
    squares: list[str] = Field(default_factory=list)

class Explanation(BaseModel):
    text: str

class Verification(BaseModel):
    status: str
    engine: str
    engine_binary_digest: str | None = None
    nodes: int | None = None
    multipv: int | None = None
    verified_at: datetime | None = None

class MasteryImpact(BaseModel):
    skill_key: str
    delta: float

class LessonSpec(BaseModel):
    schema_version: str
    lesson_id: str
    source: Source
    diagnosis: Diagnosis
    objective: Objective
    interaction: Interaction
    hints: list[Hint] = Field(default_factory=list)
    explanation: Explanation | None = None
    verification: Verification
    mastery: MasteryImpact | None = None
