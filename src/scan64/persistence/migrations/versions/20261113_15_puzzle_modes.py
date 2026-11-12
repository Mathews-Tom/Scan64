from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_15"
down_revision: str | Sequence[str] | None = "20261113_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("puzzlemodebinding"):
        return
    op.create_table(
        "puzzlemodebinding",
        sa.Column("run_id", sa.String(), sa.ForeignKey("puzzlerun.id"), primary_key=True),
        sa.Column("player_id", sa.String(), sa.ForeignKey("player.id"), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("theme", sa.String(), nullable=True),
        sa.Column("widening", sa.String(), nullable=False),
    )
    op.create_index("ix_puzzlemodebinding_player_id", "puzzlemodebinding", ["player_id"])


def downgrade() -> None:
    # Export mode history before downgrade; existing puzzle runs remain unchanged.
    op.drop_table("puzzlemodebinding")
