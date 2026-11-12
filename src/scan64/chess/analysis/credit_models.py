"""Prospective, independently replayable positive evidence; never a diagnosis."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlmodel import JSON, Field, SQLModel


class PositiveMoveCredit(SQLModel, table=True):
    player_id: str = Field(primary_key=True)
    game_id: UUID = Field(primary_key=True, foreign_key="game.id")
    canonical_id: str = Field(primary_key=True)
    skill_id: str = Field(primary_key=True)
    ply: int
    source_analysis_id: UUID = Field(foreign_key="engineanalysis.id")
    reached_analysis_id: UUID = Field(foreign_key="engineanalysis.id")
    proof: dict[str, Any] = Field(sa_type=JSON)
    proof_version: int = 1
    credited_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
