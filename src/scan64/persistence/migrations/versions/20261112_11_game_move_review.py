from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_11"
down_revision: str | Sequence[str] | None = "20261112_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy startup may adopt a schema built from SQLModel.metadata.
    if sa.inspect(op.get_bind()).has_table("gamemovereview"):
        return
    op.create_table(
        "gamemovereview",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("ply", sa.Integer(), nullable=False),
        sa.Column("source_position_id", sa.Uuid(), nullable=False),
        sa.Column("reached_position_id", sa.Uuid(), nullable=False),
        sa.Column("before_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("after_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("move_uci", sa.String(), nullable=False),
        sa.Column("san", sa.String(), nullable=False),
        sa.Column("mover", sa.String(), nullable=False),
        sa.Column("before_probability", sa.Float(), nullable=True),
        sa.Column("after_probability", sa.Float(), nullable=True),
        sa.Column("before_terminal", sa.String(), nullable=True),
        sa.Column("after_terminal", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["game.id"]),
        sa.ForeignKeyConstraint(["source_position_id"], ["position.id"]),
        sa.ForeignKeyConstraint(["reached_position_id"], ["position.id"]),
        sa.ForeignKeyConstraint(["before_analysis_id"], ["engineanalysis.id"]),
        sa.ForeignKeyConstraint(["after_analysis_id"], ["engineanalysis.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "ply", name="uq_game_review_ply"),
    )
    op.create_index("ix_gamemovereview_game_id", "gamemovereview", ["game_id"])


def downgrade() -> None:
    op.drop_index("ix_gamemovereview_game_id", table_name="gamemovereview")
    op.drop_table("gamemovereview")
