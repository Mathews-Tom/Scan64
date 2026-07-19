from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExplanationClaim(BaseModel):
    """A structured factual claim that can be checked against verified evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    line: tuple[str, ...] = ()
    certainty: Literal["observed", "likely", "certain"] = "observed"
    disclosure_level: int = Field(ge=1)
