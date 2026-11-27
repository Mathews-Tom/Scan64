from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import UniqueConstraint
from sqlmodel import JSON, Field, SQLModel


class EngineAnalysisConfig(BaseModel):
    engine_name: str
    engine_version: str
    network: str | None = None
    options: dict[str, Any] = {}
    nodes: int | None = None
    depth: int | None = None
    time_ms: int | None = None
    multipv: int = 1
    # Absent (``None``) at the default scale of 1, so rows written before
    # ``engine.node_scale`` existed keep the same reuse key.
    node_scale: float | None = None


class EngineLine(BaseModel):
    """Typed read of one stored ``EngineAnalysis.raw_result`` line.

    Rows written before the line extension carry only ``pv`` and one of
    ``score_cp``/``score_mate``; every extended field is then ``None``. Scores
    and ``wdl`` (wins, draws, losses per mille) are from the side to move.
    ``nps`` is wall-clock dependent: record it, never compare or key on it.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    pv: list[str] = []
    score_cp: int | None = None
    score_mate: int | None = None
    wdl: tuple[int, int, int] | None = None
    depth: int | None = None
    seldepth: int | None = None
    nodes: int | None = None
    nps: int | None = None
    multipv: int | None = None


class EngineAnalysis(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    position_id: UUID = Field(foreign_key="position.id")
    config: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # One dict per MultiPV line, best first: {"pv": [...], "score_cp": 50} plus,
    # when the engine reports them, "wdl", "depth", "seldepth", "nodes", "nps"
    # and "multipv" (see ``EngineLine``). Absent keys mean "not reported".
    raw_result: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)

    def lines(self) -> list[EngineLine]:
        return [EngineLine.model_validate(line) for line in self.raw_result]


class AnalysisJob(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    game_id: UUID = Field(foreign_key="game.id")
    status: str = "pending"  # pending, running, completed, failed
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    error: str | None = None


class PersistedDiagnosis(BaseModel):
    primary: str
    secondary: list[str]
    confidence: float


class PersistedLessonOpportunity(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    game_id: UUID = Field(foreign_key="game.id", index=True)
    source_position_id: UUID = Field(foreign_key="position.id", index=True)
    player_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lesson_spec: dict[str, Any] = Field(sa_type=JSON)
    verification_status: str = Field(default="unverified", index=True)
    verification_analysis_id: UUID | None = Field(default=None, index=True)
    verification_error: str | None = None


class GameMoveReview(SQLModel, table=True):
    """Immutable fast-pass inputs, identified by occurrence rather than FEN."""

    __table_args__ = (UniqueConstraint("game_id", "ply", name="uq_game_review_ply"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    game_id: UUID = Field(foreign_key="game.id", index=True)
    ply: int
    source_position_id: UUID = Field(foreign_key="position.id")
    reached_position_id: UUID = Field(foreign_key="position.id")
    before_analysis_id: UUID = Field(foreign_key="engineanalysis.id")
    after_analysis_id: UUID = Field(foreign_key="engineanalysis.id")
    move_uci: str
    san: str
    mover: str
    before_probability: float | None = None
    after_probability: float | None = None
    before_terminal: str | None = None
    after_terminal: str | None = None
