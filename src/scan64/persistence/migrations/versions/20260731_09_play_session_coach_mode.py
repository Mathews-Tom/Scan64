from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_09"
down_revision: str | Sequence[str] | None = "20260730_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("playsession"):
        return
    columns = {column["name"] for column in inspector.get_columns("playsession")}
    if "coach_mode" not in columns:
        with op.batch_alter_table("playsession") as batch:
            batch.add_column(
                sa.Column("coach_mode", sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("playsession"):
        return
    columns = {column["name"] for column in inspector.get_columns("playsession")}
    if "coach_mode" in columns:
        with op.batch_alter_table("playsession") as batch:
            batch.drop_column("coach_mode")
