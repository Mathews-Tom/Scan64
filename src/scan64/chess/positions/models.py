from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Position(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    game_id: UUID | None = Field(default=None, foreign_key="game.id")
    fen: str
    half_move_clock: int = 0
    full_move_number: int = 1
    side_to_move: str # 'w' or 'b'
    canonical_id: str # typically board FEN without move numbers/clocks
