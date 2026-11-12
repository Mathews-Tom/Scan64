from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_10"
down_revision: str | Sequence[str] | None = "20260731_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("game")}
    if "owner_color" in columns:
        return
    with op.batch_alter_table("game") as batch:
        batch.add_column(sa.Column("owner_color", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("game") as batch:
        batch.drop_column("owner_color")
