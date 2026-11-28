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
from scan64.explanations.voice import (
    DEFAULT_NARRATOR_VOICE,
    NarratorVoice,
    is_narrator_voice,
)
from scan64.providers.llm.adapters import LLMExplanationProvider, LLMProviderError
from scan64.providers.llm.contracts import ExplanationRequest, GeneratedExplanation
from scan64.providers.llm.local_ollama import LocalOllamaExplanationProvider

OPERATOR_LLM_CONFIG = "SCAN64_LLM_CONFIG"

logger = logging.getLogger(__name__)


class OwnerExplanationSettings(SQLModel, table=True):
    player_id: str = Field(primary_key=True, foreign_key="player.id")
    model: str = Field(min_length=1, max_length=128)
    revision: str = Field(default_factory=lambda: uuid4().hex, max_length=32)
    # Narrator voice (#387). NULL (rows saved before #387) means the default voice.
    voice: str | None = Field(default=None, max_length=16)


def stored_narrator_voice(row: OwnerExplanationSettings) -> NarratorVoice:
    """The row's voice; NULL or an unknown name reads as the default voice."""
    return row.voice if is_narrator_voice(row.voice) else DEFAULT_NARRATOR_VOICE


class LocalNarratorProvider:
    """The owner's local provider and the narrator voice saved with the grant."""

    def __init__(
        self, provider: LLMExplanationProvider, voice: NarratorVoice = DEFAULT_NARRATOR_VOICE
    ) -> None:
        self.provider = provider
        self.voice: NarratorVoice = voice

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        return await self.provider.generate(request)

    def supports_rewrite(self) -> bool:
        return callable(getattr(self.provider, "rewrite", None))

    async def rewrite(self, system: str, user: str) -> str:
        """Plain text from the owner's provider (the voice pipeline, #454)."""
        rewrite = getattr(self.provider, "rewrite", None)
        if not callable(rewrite):
            raise LLMProviderError("The local provider cannot write plain sentences")
        text = await rewrite(system, user)
        if not isinstance(text, str):
            raise LLMProviderError("The local provider returned no text")
        return text


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
) -> tuple[str, str, NarratorVoice] | None:
    try:
        with Session(engine) as settings_session:
            row = settings_session.exec(
                select(
                    OwnerExplanationSettings.model,
                    OwnerExplanationSettings.revision,
                    local_explanation_owner_generation(player_id),
                    OwnerExplanationSettings.voice,
                )
                .select_from(Player)
                .join(OwnerExplanationSettings)
                .where(Player.id == player_id)
            ).first()
            if row is None or row[2] != owner_generation:
                return None
            voice = row[3] if is_narrator_voice(row[3]) else DEFAULT_NARRATOR_VOICE
            return row[0], row[1], voice
    except SQLAlchemyError:
        # Optional wording must not fail analysis or poison its separate transaction.
        logger.warning("Local explanation consent unavailable")
        return None


async def read_local_explanation_provider(
    session: Session, player_id: str, owner_generation: int | None
) -> LocalNarratorProvider | None:
    """Read a live grant for the account that started this generation.

    The voice is read with the grant; a later voice change does not revoke it.
    """
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
        return current is not None and current[:2] == consent[:2]

    return LocalNarratorProvider(
        LocalOllamaExplanationProvider(consent[0], still_authorized), consent[2]
    )
