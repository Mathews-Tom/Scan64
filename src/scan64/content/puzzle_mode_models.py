"""Immutable player-owned practice-mode snapshots; daily runs have no binding."""

from sqlmodel import Field, SQLModel


class PuzzleModeBinding(SQLModel, table=True):
    run_id: str = Field(primary_key=True, foreign_key="puzzlerun.id")
    player_id: str = Field(foreign_key="player.id", index=True)
    mode: str
    theme: str | None = Field(default=None)
    widening: str
