from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_06"
down_revision: str | Sequence[str] | None = "20260729_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("persistedlessonopportunity"):
        return

    columns = {column["name"] for column in inspector.get_columns("persistedlessonopportunity")}
    with op.batch_alter_table("persistedlessonopportunity") as batch:
        if "verification_status" not in columns:
            batch.add_column(
                sa.Column(
                    "verification_status",
                    sa.String(),
                    nullable=False,
                    server_default="unverified",
                )
            )
            batch.create_index(
                "ix_persistedlessonopportunity_verification_status",
                ["verification_status"],
            )
        if "verification_error" not in columns:
            batch.add_column(sa.Column("verification_error", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("persistedlessonopportunity"):
        return

    columns = {column["name"] for column in inspector.get_columns("persistedlessonopportunity")}
    index_names = {index["name"] for index in inspector.get_indexes("persistedlessonopportunity")}
    with op.batch_alter_table("persistedlessonopportunity") as batch:
        if "ix_persistedlessonopportunity_verification_status" in index_names:
            batch.drop_index("ix_persistedlessonopportunity_verification_status")
        if "verification_error" in columns:
            batch.drop_column("verification_error")
        if "verification_status" in columns:
            batch.drop_column("verification_status")
