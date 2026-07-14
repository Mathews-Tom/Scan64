from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from scan64.content.models import ContentItem


class AssetItem(BaseModel):
    asset_type: Literal["game_score", "study_material"]
    source_url: str
    licence: str
    content_identifier: str

    @model_validator(mode="after")
    def validate_source(self) -> AssetItem:
        if not self.source_url.startswith("https://"):
            raise ValueError("asset source_url must use HTTPS")
        if not self.licence:
            raise ValueError("asset licence must not be empty")
        if not self.content_identifier:
            raise ValueError("asset content_identifier must not be empty")
        return self


class VerifiedAlternative(BaseModel):
    san: str
    explanation: str


class FamousGameDecision(BaseModel):
    id: str
    ply: int = Field(ge=0)
    fen: str
    prompt: str
    played_move: str
    accepted_moves: list[str] = Field(min_length=1)
    verified_alternatives: list[VerifiedAlternative] = Field(min_length=1)
    hints: list[str] = Field(min_length=1)
    comparison: str

    @model_validator(mode="after")
    def validate_played_move(self) -> FamousGameDecision:
        if self.played_move not in self.accepted_moves:
            raise ValueError("accepted_moves must include played_move")
        return self


class FamousGamePayload(BaseModel):
    title: str
    historical_context: str
    strategic_context: str
    moves: list[str] = Field(min_length=24)
    decisions: list[FamousGameDecision] = Field(min_length=1)


class FamousGameDefinition(BaseModel):
    id: str
    version: str = "1.0"
    payload: FamousGamePayload
    assets: list[AssetItem] = Field(min_length=2)
    skill_mapping: dict[str, float]
    difficulty_estimate: float = Field(default=1500.0, gt=0)

    @model_validator(mode="after")
    def validate_assets(self) -> FamousGameDefinition:
        asset_types = {asset.asset_type for asset in self.assets}
        if asset_types != {"game_score", "study_material"}:
            raise ValueError("famous game assets must include score and study material")
        if any(weight <= 0 for weight in self.skill_mapping.values()):
            raise ValueError("skill mapping weights must be positive")
        return self

    def to_content_item(self) -> ContentItem:
        score_asset = next(asset for asset in self.assets if asset.asset_type == "game_score")
        payload = self.payload.model_dump(mode="json")
        payload["assets"] = [asset.model_dump(mode="json") for asset in self.assets]
        return ContentItem(
            id=self.id,
            domain="famous_games",
            version=self.version,
            payload=payload,
            provenance=score_asset.source_url,
            licence=score_asset.licence,
            skill_mapping=self.skill_mapping,
            difficulty_estimate=self.difficulty_estimate,
        )

