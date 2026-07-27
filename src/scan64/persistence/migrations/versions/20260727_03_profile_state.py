from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_03"
down_revision: str | Sequence[str] | None = "20260727_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("profileobservation"):
        op.create_table(
            "profileobservation",
            sa.Column("player_id", sa.String(), primary_key=True),
            sa.Column("game_id", sa.String(), primary_key=True),
            sa.Column("position_id", sa.String(), primary_key=True),
            sa.Column("skill_id", sa.String(), primary_key=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        )
    for table_name in ("skillstate", "reviewschedule"):
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        with op.batch_alter_table(table_name) as batch:
            if "retired_at" not in columns:
                batch.add_column(sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
            if "retirement_reason" not in columns:
                batch.add_column(sa.Column("retirement_reason", sa.String(), nullable=True))
            if table_name == "reviewschedule" and "skill_id" not in columns:
                batch.add_column(sa.Column("skill_id", sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in ("skillstate", "reviewschedule"):
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        with op.batch_alter_table(table_name) as batch:
            if table_name == "reviewschedule" and "skill_id" in columns:
                batch.drop_column("skill_id")
            if "retirement_reason" in columns:
                batch.drop_column("retirement_reason")
            if "retired_at" in columns:
                batch.drop_column("retired_at")
    if inspector.has_table("profileobservation"):
        op.drop_table("profileobservation")
