from typing import Any

from pydantic import BaseModel, Field

from scan64.content.models import ContentItem


class OpeningMission(BaseModel):
    id: str
    description: str
    invariant_type: str
    invariant_args: dict[str, Any] = Field(default_factory=dict)


class OpeningFamilyPayload(BaseModel):
    name: str
    instructional_purpose: str
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
