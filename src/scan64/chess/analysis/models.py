from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import UniqueConstraint
from sqlmodel import JSON, Field, SQLModel


class EngineAnalysisConfig(BaseModel):
    engine_name: str
    engine_version: str
    network: str | None = None
    nodes: int | None = None
    depth: int | None = None
    time_ms: int | None = None
    multipv: int = 1


class EngineAnalysis(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    position_id: UUID = Field(foreign_key="position.id")
    config: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Store the result, e.g., MultiPV dict
    # Each entry in the list could be a dict like {"pv": ["e2e4", "e7e5"], "score_cp": 50}
    raw_result: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)


class AnalysisJob(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    game_id: UUID = Field(foreign_key="game.id")
    status: str = "pending"  # pending, running, completed, failed
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    error: str | None = None


class PersistedDiagnosis(BaseModel):
    primary: str
    secondary: list[str]
    confidence: float


class PersistedLessonOpportunity(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    game_id: UUID = Field(foreign_key="game.id", index=True)
    source_position_id: UUID = Field(foreign_key="position.id", index=True)
    player_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lesson_spec: dict[str, Any] = Field(sa_type=JSON)
    verification_status: str = Field(default="unverified", index=True)
    verification_analysis_id: UUID | None = Field(default=None, index=True)
    verification_error: str | None = None


class GameMoveReview(SQLModel, table=True):
    """Immutable fast-pass inputs, identified by occurrence rather than FEN."""

    __table_args__ = (UniqueConstraint("game_id", "ply", name="uq_game_review_ply"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    game_id: UUID = Field(foreign_key="game.id", index=True)
    ply: int
    source_position_id: UUID = Field(foreign_key="position.id")
    reached_position_id: UUID = Field(foreign_key="position.id")
    before_analysis_id: UUID = Field(foreign_key="engineanalysis.id")
    after_analysis_id: UUID = Field(foreign_key="engineanalysis.id")
    move_uci: str
    san: str
    mover: str
    before_probability: float | None = None
    after_probability: float | None = None
    before_terminal: str | None = None
    after_terminal: str | None = None
