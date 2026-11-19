from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictInt
from pydantic import Field as ModelField
from sqlalchemy import DateTime, String
from sqlmodel import Column, Field, SQLModel

ClockColor = Literal["white", "black"]
ClockTerminalReason = Literal["flag", "board", "resignation"]


class TimeControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_seconds: StrictInt = ModelField(ge=1, le=86400)
    increment_seconds: StrictInt = ModelField(default=0, ge=0, le=60)


class PlayClock(SQLModel, table=True):
    session_id: UUID = Field(primary_key=True, foreign_key="playsession.id")
    initial_seconds: StrictInt = Field(ge=1, le=86400)
    increment_seconds: StrictInt = Field(ge=0, le=60)
    white_remaining_ms: StrictInt = Field(ge=0)
    black_remaining_ms: StrictInt = Field(ge=0)
    active_color: ClockColor | None = Field(default=None, sa_column=Column(String, nullable=True))
    deadline_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True, index=True)
    )
    version: StrictInt = Field(default=0, ge=0)
    terminal_reason: ClockTerminalReason | None = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    flagged_color: ClockColor | None = Field(default=None, sa_column=Column(String, nullable=True))


class PlayClockRead(BaseModel):
    initial_seconds: int
    increment_seconds: int
    white_remaining_ms: int
    black_remaining_ms: int
    active_color: ClockColor | None
    deadline_at: datetime | None
    server_now: datetime
    version: int
    terminal_reason: ClockTerminalReason | None
    flagged_color: ClockColor | None
