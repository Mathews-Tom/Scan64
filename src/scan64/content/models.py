import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class ContentItem(SQLModel, table=True):
    """
    Structured chess content (opening, tactic, endgame, famous game)
    with versioning, provenance, licence, and skill-mapping metadata.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    domain: str = Field(index=True)  # e.g. "tactics", "endgames", "openings"
    version: str = Field(default="1.0")

    # Domain-specific payload (e.g., FEN, moves, lines)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Provenance and Licence metadata (required for acceptance)
    provenance: str = Field(
        description="Source of the content (e.g., Lichess puzzle DB, specific game, author)"
    )
    licence: str = Field(description="Licence of the content (e.g., CC0, CC BY-SA 4.0)")

    # Skill mapping: maps concept_code to weight (e.g., {"tactics.fork": 1.0})
    skill_mapping: dict[str, float] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="Mapping of skill codes to their importance/weight in this item",
    )

    # Difficulty estimate (e.g. Elo-like)
    difficulty_estimate: float = Field(default=1500.0)


class StudySession(SQLModel, table=True):
    """
    A conventional or adaptive sequence over content items for a specific learner.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    player_id: str = Field(index=True)
    domain: str = Field(index=True)

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)


class ContentAttempt(SQLModel, table=True):
    """
    Learner response to a content item decision.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str | None = Field(default=None, foreign_key="studysession.id", index=True)
    item_id: str = Field(foreign_key="contentitem.id", index=True)
    player_id: str = Field(index=True)

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)

    success: bool = Field(default=False)
    hint_assisted: bool = Field(default=False)

    # Specifics of what the learner answered, hints used, etc.
    response_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
