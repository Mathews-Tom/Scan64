from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_04"
down_revision: str | Sequence[str] | None = "20260727_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if (
        inspector.has_table("lessonattempt")
        or not inspector.has_table("studysession")
        or not inspector.has_table("persistedlessonopportunity")
    ):
        return
    op.create_table(
        "lessonattempt",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("lesson_id", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_move", sa.String(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("hints_used", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("grading_status", sa.String(), nullable=False),
        sa.Column("profile_update_result", sa.String(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["studysession.id"]),
        sa.ForeignKeyConstraint(["opportunity_id"], ["persistedlessonopportunity.id"]),
    )
    for column in ("session_id", "player_id", "lesson_id", "source_kind", "opportunity_id"):
        op.create_index(f"ix_lessonattempt_{column}", "lessonattempt", [column])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("lessonattempt"):
        return
    for column in ("session_id", "player_id", "lesson_id", "source_kind", "opportunity_id"):
        op.drop_index(f"ix_lessonattempt_{column}", table_name="lessonattempt")
    op.drop_table("lessonattempt")
