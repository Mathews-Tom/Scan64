"""Add isolated player-owned community lesson packs; no learning-state changes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261124_25"
down_revision: str | Sequence[str] | None = "20261122_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    if not sa.inspect(connection).has_table("lessonpack"):
        op.create_table(
            "lessonpack",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("pack_id", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("author", sa.String(), nullable=False),
            sa.Column("licence", sa.String(), nullable=False),
            sa.Column("version", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("sha256", sa.String(), nullable=False),
            sa.Column("installed_at", sa.DateTime(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("reason", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["player_id"], ["player.id"]),
            sa.UniqueConstraint("player_id", "pack_id", name="uq_lessonpack_owner_slug"),
            sa.CheckConstraint(
                "state IN ('pending', 'verifying', 'verified', 'failed', 'unavailable')",
                name="ck_lessonpack_state",
            ),
        )
        op.create_index("ix_lessonpack_player_id", "lessonpack", ["player_id"])
    if not sa.inspect(connection).has_table("packlesson"):
        op.create_table(
            "packlesson",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("pack_id", sa.Uuid(), nullable=False),
            sa.Column("lesson_id", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("failing_ply", sa.Integer(), nullable=True),
            sa.Column("reason", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["pack_id"], ["lessonpack.id"]),
            sa.UniqueConstraint("pack_id", "lesson_id", name="uq_packlesson_pack_slug"),
            sa.CheckConstraint(
                "state IN ('pending', 'verified', 'rejected')", name="ck_packlesson_state"
            ),
            sa.CheckConstraint(
                "failing_ply IS NULL OR failing_ply >= 1", name="ck_packlesson_failing_ply"
            ),
        )
        op.create_index("ix_packlesson_pack_id", "packlesson", ["pack_id"])


def downgrade() -> None:
    # These tables contain only reinstallable local files, never player progress.
    op.drop_index("ix_packlesson_pack_id", table_name="packlesson")
    op.drop_table("packlesson")
    op.drop_index("ix_lessonpack_player_id", table_name="lessonpack")
    op.drop_table("lessonpack")
