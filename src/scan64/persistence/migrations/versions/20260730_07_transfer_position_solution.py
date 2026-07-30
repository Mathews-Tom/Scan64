from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_07"
down_revision: str | Sequence[str] | None = "20260730_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("transferposition"):
        return
    columns = {column["name"] for column in inspector.get_columns("transferposition")}
    if "solution_uci" not in columns:
        with op.batch_alter_table("transferposition") as batch:
            batch.add_column(sa.Column("solution_uci", sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("transferposition"):
        return
    columns = {column["name"] for column in inspector.get_columns("transferposition")}
    if "solution_uci" in columns:
        with op.batch_alter_table("transferposition") as batch:
            batch.drop_column("solution_uci")
