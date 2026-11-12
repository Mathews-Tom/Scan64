from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_16"
down_revision: str | Sequence[str] | None = "20261113_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("playeronboarding"):
        return
    op.create_table(
        "playeronboarding",
        sa.Column("player_id", sa.String(), sa.ForeignKey("player.id"), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("declared_level", sa.String(), nullable=True),
        sa.Column("suggested_bot_level", sa.Integer(), nullable=True),
        sa.Column("mapping_version", sa.String(), nullable=True),
        sa.Column("intro_started_at", sa.DateTime(), nullable=True),
        sa.Column("skipped_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    # Existing profiles remain unplaced: no inference from preferences or measured skills.
    if sa.inspect(op.get_bind()).has_table("player"):
        op.execute(
            sa.text(
                "INSERT INTO playeronboarding (player_id, status) "
                "SELECT id, 'not_offered' FROM player"
            )
        )


def downgrade() -> None:
    # The orientation table owns no puzzle history, ratings, mastery or preferences.
    op.drop_table("playeronboarding")
