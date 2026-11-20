"""Add typed owned sparring attempts without changing source reviews or games."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_20"
down_revision: str | Sequence[str] | None = "20261119_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy startup may already have created the current SQLModel metadata.
    if sa.inspect(op.get_bind()).has_table("sparringattempt"):
        return
    op.create_table(
        "sparringattempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("source_review_id", sa.Uuid(), nullable=False),
        sa.Column("source_position_id", sa.Uuid(), nullable=False),
        sa.Column("source_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("play_session_id", sa.Uuid(), nullable=False),
        sa.Column("source_ply", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("verification_status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["source_review_id"], ["gamemovereview.id"]),
        sa.ForeignKeyConstraint(["source_position_id"], ["position.id"]),
        sa.ForeignKeyConstraint(["source_analysis_id"], ["engineanalysis.id"]),
        sa.ForeignKeyConstraint(["play_session_id"], ["playsession.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("play_session_id", name="uq_sparring_play_session"),
        sa.CheckConstraint("source_ply >= 0", name="ck_sparring_source_ply"),
        sa.CheckConstraint(
            "verification_status IN ('verified', 'historical')", name="ck_sparring_verification"
        ),
        sa.CheckConstraint(
            "(completed_at IS NULL AND result IS NULL AND outcome IS NULL) OR "
            "(completed_at IS NOT NULL AND result IS NOT NULL AND outcome IS NOT NULL "
            "AND result IN ('1-0', '0-1', '1/2-1/2') AND outcome IN ('win', 'draw', 'loss'))",
            name="ck_sparring_completion",
        ),
    )
    op.create_index("ix_sparringattempt_player_id", "sparringattempt", ["player_id"])
    op.create_index("ix_sparringattempt_source_review_id", "sparringattempt", ["source_review_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if not sa.inspect(connection).has_table("sparringattempt"):
        return
    if connection.execute(sa.text("SELECT 1 FROM sparringattempt LIMIT 1")).first() is not None:
        raise RuntimeError(
            "Cannot downgrade with sparring attempts present; retain an export and restore "
            "the reviewed pre-migration backup instead of discarding training history"
        )
    op.drop_index("ix_sparringattempt_source_review_id", table_name="sparringattempt")
    op.drop_index("ix_sparringattempt_player_id", table_name="sparringattempt")
    op.drop_table("sparringattempt")
