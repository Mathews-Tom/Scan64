from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from scan64.content.models import ContentItem


class OpeningMission(BaseModel):
    id: str
    description: str
    invariant_type: str
    invariant_args: dict[str, Any] = Field(default_factory=dict)


class OpeningFamilyPayload(BaseModel):
    family_id: str
    name: str
    instructional_purpose: str
    structure: Literal["open_centre", "queen_pawn", "solid_defence"]
    learner_colour: Literal["white", "black"]
    opponent_response_moves: list[str] = Field(min_length=1)
    moves: list[str] = Field(default_factory=list)
    missions: list[OpeningMission] = Field(default_factory=list)


def create_opening_family_item(
    *, provenance: str, licence: str, payload: OpeningFamilyPayload, **kwargs: Any
) -> ContentItem:
    return ContentItem(
        domain="openings",
        payload=payload.model_dump(mode="json"),
        provenance=provenance,
        licence=licence,
        **kwargs,
    )
