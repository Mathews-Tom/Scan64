from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class CoachStudentLink(SQLModel, table=True):
    coach_id: str = Field(primary_key=True, foreign_key="player.id")
    student_id: str = Field(primary_key=True, foreign_key="player.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
