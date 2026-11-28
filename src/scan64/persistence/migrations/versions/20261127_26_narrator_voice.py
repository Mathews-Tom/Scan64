"""Add the narrator voice to installation-local explanation settings (#387).

Additive: a nullable column. NULL (every row saved before this revision) means the
default voice, so existing grants keep working unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261127_26"
down_revision: str | Sequence[str] | None = "20261124_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ownerexplanationsettings"


def _has_voice() -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(_TABLE)
    return any(column["name"] == "voice" for column in columns)


def upgrade() -> None:
    # Legacy startup may already have created the current SQLModel metadata.
    if _has_voice():
        return
    # SQLite adds a nullable column in place; no table rebuild.
    op.add_column(_TABLE, sa.Column("voice", sa.String(16), nullable=True))


def downgrade() -> None:
    if not _has_voice():
        return
    op.drop_column(_TABLE, "voice")
