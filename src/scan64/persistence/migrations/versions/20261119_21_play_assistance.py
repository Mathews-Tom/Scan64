"""Retain explicit Candidates and guard provenance on existing Play disclosures.

Legacy rows retain their exact hint steps and gain false source flags. Downgrade
refuses new disclosures rather than discard their source or assistance meaning.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_21"
down_revision: str | Sequence[str] | None = "20261119_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "sessionhintreveal"
FLAGS = ("candidate_used", "guard_used")


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if TABLE not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    for name in FLAGS:
        if name not in columns:
            op.add_column(
                TABLE, sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if TABLE not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    flags = [name for name in FLAGS if name in columns]
    if not flags:
        return
    disclosures = sa.table(TABLE, *(sa.column(name, sa.Boolean()) for name in flags))
    used = connection.scalar(
        sa.select(sa.literal(1))
        .select_from(disclosures)
        .where(sa.or_(*(disclosures.c[name].is_(True) for name in flags)))
        .limit(1)
    )
    if used is not None:
        raise RuntimeError(
            "Cannot downgrade Play assistance after Candidates or guard disclosure; "
            "retain the current schema or restore the pre-migration backup."
        )
    # Partial legacy schemas can retain this child without its PlaySession parent.
    with op.batch_alter_table(TABLE, reflect_kwargs={"resolve_fks": False}) as batch:
        for name in reversed(flags):
            batch.drop_column(name)
