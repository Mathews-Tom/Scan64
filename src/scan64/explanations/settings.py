"""Installation-local consent: never a player preference imported from an archive."""

from __future__ import annotations

import asyncio
import logging
from os import environ
from uuid import uuid4

from sqlalchemy import Connection, Engine, SQLColumnExpression, func, literal
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Field, Session, SQLModel, col, select

from scan64.api.models import DeletionAudit, Player
from scan64.providers.llm.local_ollama import LocalOllamaExplanationProvider

OPERATOR_LLM_CONFIG = "SCAN64_LLM_CONFIG"

logger = logging.getLogger(__name__)


class OwnerExplanationSettings(SQLModel, table=True):
    player_id: str = Field(primary_key=True, foreign_key="player.id")
    model: str = Field(min_length=1, max_length=128)
    revision: str = Field(default_factory=lambda: uuid4().hex, max_length=32)


def operator_manages_explanations() -> bool:
    return OPERATOR_LLM_CONFIG in environ


def _engine(session: Session) -> Engine:
    bind = session.get_bind()
    return bind.engine if isinstance(bind, Connection) else bind


def local_explanation_owner_generation(
    player_id: str | SQLColumnExpression[str | None],
) -> ColumnElement[int | None]:
    """Select the local account generation in the evidence snapshot's transaction."""
    if operator_manages_explanations():
        return literal(None)
    # Account deletion appends a local tombstone atomically. An archive can
    # restore player attributes, but cannot roll back this counter.
    return (
        select(func.count(col(DeletionAudit.id)))
        .select_from(Player)
        .outerjoin(DeletionAudit, col(DeletionAudit.player_id) == col(Player.id))
        .where(col(Player.id) == player_id)
        .group_by(Player.id)
        .correlate_except(Player, DeletionAudit)
        .scalar_subquery()
    )


def _read_committed_consent(
    engine: Engine, player_id: str, owner_generation: int
) -> tuple[str, str] | None:
    try:
        with Session(engine) as settings_session:
            row = settings_session.exec(
                select(
                    OwnerExplanationSettings.model,
                    OwnerExplanationSettings.revision,
                    local_explanation_owner_generation(player_id),
                )
                .select_from(Player)
                .join(OwnerExplanationSettings)
                .where(Player.id == player_id)
            ).first()
            if row is None or row[2] != owner_generation:
                return None
            return row[0], row[1]
    except SQLAlchemyError:
        # Optional wording must not fail analysis or poison its separate transaction.
        logger.warning("Local explanation consent unavailable")
        return None


async def read_local_explanation_provider(
    session: Session, player_id: str, owner_generation: int | None
) -> LocalOllamaExplanationProvider | None:
    """Read a live grant for the account that started this generation."""
    if owner_generation is None or operator_manages_explanations():
        return None
    engine = _engine(session)
    consent = await asyncio.to_thread(_read_committed_consent, engine, player_id, owner_generation)
    if consent is None:
        return None

    async def still_authorized() -> bool:
        current = await asyncio.to_thread(
            _read_committed_consent, engine, player_id, owner_generation
        )
        return current == consent

    return LocalOllamaExplanationProvider(consent[0], still_authorized)
