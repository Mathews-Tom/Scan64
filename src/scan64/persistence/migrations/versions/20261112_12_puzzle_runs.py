from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_12"
down_revision: str | Sequence[str] | None = "20261112_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("puzzlerun"):
        return
    op.create_table(
        "puzzlerun",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("fen", sa.String(), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("wrong_attempts", sa.Integer(), nullable=False),
        sa.Column("played_moves", sa.JSON(), nullable=False),
        sa.Column("hint_steps", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["player.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["contentitem.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["studysession.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("player_id", "item_id", "session_id"):
        op.create_index(f"ix_puzzlerun_{column}", "puzzlerun", [column])
    op.create_index(
        "uq_puzzlerun_active_player",
        "puzzlerun",
        ["player_id"],
        unique=True,
        sqlite_where=sa.text("state = 'active'"),
        postgresql_where=sa.text("state = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("puzzlerun")
