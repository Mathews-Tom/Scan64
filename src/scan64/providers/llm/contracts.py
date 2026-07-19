from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMMessage(BaseModel):
    """One prompt message sent to an explanation provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user"]
    content: str = Field(min_length=1)


class ExplanationClaim(BaseModel):
    """A structured factual claim returned by an LLM provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    line: tuple[str, ...] = ()
    certainty: Literal["observed", "likely", "certain"] = "observed"
    disclosure_level: int = Field(ge=1)


class GeneratedExplanation(BaseModel):
    """Schema-constrained explanation output before grounding validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[ExplanationClaim, ...] = Field(min_length=1)


class ExplanationRequest(BaseModel):
    """Prompt package supplied by the explanation assembly layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[LLMMessage, ...] = Field(min_length=1)
