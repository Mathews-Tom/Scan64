"""Persisted, player-owned state for solving an imported puzzle line."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, Index, text
from sqlmodel import Field, SQLModel


class PuzzleRun(SQLModel, table=True):
    __table_args__ = (
        Index(
            "uq_puzzlerun_active_player",
            "player_id",
            unique=True,
            sqlite_where=text("state = 'active'"),
            postgresql_where=text("state = 'active'"),
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    player_id: str = Field(foreign_key="player.id", index=True)
    item_id: str = Field(foreign_key="contentitem.id", index=True)
    session_id: str = Field(foreign_key="studysession.id", index=True)
    state: str = Field(default="active")
    fen: str
    cursor: int = Field(default=0)
    revision: int = Field(default=0)
    wrong_attempts: int = Field(default=0)
    played_moves: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    hint_steps: dict[str, int] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
