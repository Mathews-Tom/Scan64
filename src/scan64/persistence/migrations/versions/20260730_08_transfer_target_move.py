from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_08"
down_revision: str | Sequence[str] | None = "20260730_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("transfermeasurement"):
        return
    columns = {column["name"] for column in inspector.get_columns("transfermeasurement")}
    if "target_move_uci" not in columns:
        with op.batch_alter_table("transfermeasurement") as batch:
            batch.add_column(sa.Column("target_move_uci", sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("transfermeasurement"):
        return
    columns = {column["name"] for column in inspector.get_columns("transfermeasurement")}
    if "target_move_uci" in columns:
        with op.batch_alter_table("transfermeasurement") as batch:
            batch.drop_column("target_move_uci")
