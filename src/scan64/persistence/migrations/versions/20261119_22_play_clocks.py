"""Persist authoritative Fischer clocks without inventing historical timing."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_22"
down_revision: str | Sequence[str] | None = "20261117_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "playsession" not in tables:
        return
    if "playclock" not in tables:
        op.create_table(
            "playclock",
            sa.Column("session_id", sa.Uuid(), sa.ForeignKey("playsession.id"), primary_key=True),
            sa.Column("initial_seconds", sa.Integer(), nullable=False),
            sa.Column("increment_seconds", sa.Integer(), nullable=False),
            sa.Column("white_remaining_ms", sa.Integer(), nullable=False),
            sa.Column("black_remaining_ms", sa.Integer(), nullable=False),
            sa.Column("active_color", sa.String(), nullable=True),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("terminal_reason", sa.String(), nullable=True),
            sa.Column("flagged_color", sa.String(), nullable=True),
        )
    op.create_index("ix_playclock_deadline_at", "playclock", ["deadline_at"], if_not_exists=True)


def downgrade() -> None:
    connection = op.get_bind()
    if "playclock" not in sa.inspect(connection).get_table_names():
        return
    clocks = sa.table("playclock", sa.column("session_id", sa.Uuid()))
    if connection.execute(sa.select(clocks.c.session_id).limit(1)).first() is not None:
        raise RuntimeError(
            "Cannot downgrade while authoritative play clocks are retained; "
            "restore the reviewed pre-migration backup instead of discarding timing state."
        )
    op.drop_index("ix_playclock_deadline_at", table_name="playclock", if_exists=True)
    op.drop_table("playclock")
