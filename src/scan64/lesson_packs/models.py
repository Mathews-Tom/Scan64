"""Installation-local pack content, never attempts, schedules or skill evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel

PackState = Literal["pending", "verifying", "verified", "failed", "unavailable"]
LessonState = Literal["pending", "verified", "rejected"]


class LessonPack(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("player_id", "pack_id", name="uq_lessonpack_owner_slug"),
        CheckConstraint(
            "state IN ('pending', 'verifying', 'verified', 'failed', 'unavailable')",
            name="ck_lessonpack_state",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: str = Field(foreign_key="player.id", index=True)
    pack_id: str
    title: str
    author: str
    licence: str
    version: str
    description: str | None = None
    source: str | None = None
    sha256: str
    installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: PackState = Field(default="pending", sa_column=Column(String, nullable=False))
    reason: str | None = None


class PackLesson(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("pack_id", "lesson_id", name="uq_packlesson_pack_slug"),
        CheckConstraint("state IN ('pending', 'verified', 'rejected')", name="ck_packlesson_state"),
        CheckConstraint(
            "failing_ply IS NULL OR failing_ply >= 1", name="ck_packlesson_failing_ply"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pack_id: UUID = Field(foreign_key="lessonpack.id", index=True)
    lesson_id: str
    title: str
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    state: LessonState = Field(default="pending", sa_column=Column(String, nullable=False))
    failing_ply: int | None = None
    reason: str | None = None
