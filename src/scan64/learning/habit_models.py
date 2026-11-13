"""Versioned habit evidence; these rows never confer mastery or rating credit."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

HABIT_POLICY_VERSION = "habit-v1"


class HabitPolicy(SQLModel, table=True):
    player_id: str = Field(primary_key=True)
    policy_version: str = HABIT_POLICY_VERSION
    effective_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HabitGoal(SQLModel, table=True):
    player_id: str = Field(primary_key=True)
    effective_day: str = Field(primary_key=True)
    goal: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HabitDay(SQLModel, table=True):
    player_id: str = Field(primary_key=True)
    utc_day: str = Field(primary_key=True)
    goal: int
    policy_version: str = HABIT_POLICY_VERSION
    met_at: datetime | None = None


class HabitCredit(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("player_id", "utc_day", "source_key"),
        UniqueConstraint("player_id", "utc_day", "source_epd"),
        UniqueConstraint("player_id", "source_kind", "source_id"),
    )
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    player_id: str = Field(index=True)
    utc_day: str
    source_kind: str
    source_id: str
    source_key: str
    source_epd: str
    completed_at: datetime
    policy_version: str = HABIT_POLICY_VERSION
    trust_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class PatternTarget(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("player_id", "code", "policy_version"),)
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    player_id: str = Field(index=True)
    code: str
    policy_version: str = HABIT_POLICY_VERSION
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    pattern_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    practice: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    practice_completed_at: datetime | None = None
    window_game_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None


class PatternAward(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("player_id", "code", "policy_version", "window_start"),
        UniqueConstraint("target_id"),
    )
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    player_id: str = Field(index=True)
    target_id: str = Field(foreign_key="patterntarget.id", index=True)
    code: str
    policy_version: str = HABIT_POLICY_VERSION
    window_start: str
    game_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    awarded_at: datetime
    barrier_game_id: UUID | None = Field(default=None, foreign_key="game.id")
    barrier_analysis_job_id: UUID | None = Field(default=None, foreign_key="analysisjob.id")


class PatternAwardEvidence(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("award_id", "ordinal"),
        UniqueConstraint("award_id", "game_id"),
        CheckConstraint("ordinal >= 1 AND ordinal <= 5"),
    )
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    player_id: str = Field(index=True)
    award_id: str = Field(foreign_key="patternaward.id", index=True)
    ordinal: int = Field(ge=1, le=5)
    game_id: UUID = Field(foreign_key="game.id")
    analysis_job_id: UUID = Field(foreign_key="analysisjob.id")
    game_created_at: datetime
    job_completed_at: datetime
