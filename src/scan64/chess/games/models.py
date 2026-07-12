from uuid import UUID, uuid4

from sqlmodel import JSON, Column, Field, SQLModel


class Game(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pgn: str
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    moves: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    white: str = "Unknown"
    black: str = "Unknown"
    result: str = "*"
    date: str | None = None
