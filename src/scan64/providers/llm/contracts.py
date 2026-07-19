from __future__ import annotations

from typing import Literal

from chess_lesson_spec import ExplanationClaim
from pydantic import BaseModel, ConfigDict, Field


class LLMMessage(BaseModel):
    """One prompt message sent to an explanation provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user"]
    content: str = Field(min_length=1)




class GeneratedExplanation(BaseModel):
    """Schema-constrained explanation output before grounding validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[ExplanationClaim, ...] = Field(min_length=1)


class ExplanationRequest(BaseModel):
    """Prompt package supplied by the explanation assembly layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[LLMMessage, ...] = Field(min_length=1)
