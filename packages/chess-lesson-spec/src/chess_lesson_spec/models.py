from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from chess_lesson_spec.claims import ExplanationClaim


class DomainEventEnvelope(BaseModel):
    """Standard envelope for internal domain events."""

    event_id: str
    occurred_at: datetime
    schema_version: str
    correlation_id: str
    causation_id: str
    event_type: str
    payload: dict[str, Any]


class Source(BaseModel):
    kind: Literal["player_game", "custom", "external"]
    fen: str


class Diagnosis(BaseModel):
    primary: str
    secondary: list[str] = Field(default_factory=list)
    confidence: float
    evidence_refs: list[str] = Field(default_factory=list)


class Objective(BaseModel):
    type: str
    instruction: str


class AcceptedMove(BaseModel):
    san: str
    lan: str | None = None
    reason: str | None = None


class VisualizationCommand(BaseModel):
    """Base class for visualization commands."""

    command: str
    description: str = Field(
        ...,
        description="Human-readable description for screen readers and text-only clients",
    )


class HighlightSquareCommand(VisualizationCommand):
    command: Literal["highlight_square"] = "highlight_square"
    square: str


class HighlightRegionCommand(VisualizationCommand):
    command: Literal["highlight_region"] = "highlight_region"
    squares: list[str]


class HighlightPieceCommand(VisualizationCommand):
    command: Literal["highlight_piece"] = "highlight_piece"
    square: str


class DimIrrelevantPiecesCommand(VisualizationCommand):
    command: Literal["dim_irrelevant_pieces"] = "dim_irrelevant_pieces"


class DrawArrowCommand(VisualizationCommand):
    command: Literal["draw_arrow"] = "draw_arrow"
    origin: str
    target: str


class DrawAttackMapCommand(VisualizationCommand):
    command: Literal["draw_attack_map"] = "draw_attack_map"
    square: str


class DrawDefenceMapCommand(VisualizationCommand):
    command: Literal["draw_defence_map"] = "draw_defence_map"
    square: str


class ShowGhostPieceCommand(VisualizationCommand):
    command: Literal["show_ghost_piece"] = "show_ghost_piece"
    square: str
    piece: str


class AnimateLineCommand(VisualizationCommand):
    command: Literal["animate_line"] = "animate_line"
    moves: list[str]


class FlipBoardCommand(VisualizationCommand):
    command: Literal["flip_board"] = "flip_board"


class HideCoordinatesCommand(VisualizationCommand):
    command: Literal["hide_coordinates"] = "hide_coordinates"


class TemporarilyHidePiecesCommand(VisualizationCommand):
    command: Literal["temporarily_hide_pieces"] = "temporarily_hide_pieces"
    squares: list[str]


class ComparePositionsCommand(VisualizationCommand):
    command: Literal["compare_positions"] = "compare_positions"
    fen_a: str
    fen_b: str


VisualizationType = (
    HighlightSquareCommand
    | HighlightRegionCommand
    | HighlightPieceCommand
    | DimIrrelevantPiecesCommand
    | DrawArrowCommand
    | DrawAttackMapCommand
    | DrawDefenceMapCommand
    | ShowGhostPieceCommand
    | AnimateLineCommand
    | FlipBoardCommand
    | HideCoordinatesCommand
    | TemporarilyHidePiecesCommand
    | ComparePositionsCommand
)


class Interaction(BaseModel):
    input: str
    maximum_attempts: int
    accepted_moves: list[AcceptedMove] = Field(default_factory=list)


class Hint(BaseModel):
    level: int
    kind: str
    text: str
    squares: list[str] = Field(default_factory=list)
    visualizations: list[VisualizationType] = Field(default_factory=list)


class Explanation(BaseModel):
    text: str
    claims: list[ExplanationClaim] = Field(default_factory=list)
    visualizations: list[VisualizationType] = Field(default_factory=list)


class Verification(BaseModel):
    status: str
    engine: str
    engine_binary_digest: str | None = None
    nodes: int | None = None
    multipv: int | None = None
    verified_at: datetime | None = None


class MasteryImpact(BaseModel):
    skill_key: str
    delta: float


class LessonSpec(BaseModel):
    schema_version: str
    lesson_id: str
    source: Source
    diagnosis: Diagnosis
    objective: Objective
    interaction: Interaction
    hints: list[Hint] = Field(default_factory=list)
    explanation: Explanation | None = None
    verification: Verification
    mastery: MasteryImpact | None = None
