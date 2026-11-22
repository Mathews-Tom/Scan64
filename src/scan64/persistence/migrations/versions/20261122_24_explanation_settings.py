"""Store installation-local consent for explanation wording separately from archives."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_24"
down_revision: str | Sequence[str] | None = "20261122_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy startup may already have created the current SQLModel metadata.
    if sa.inspect(op.get_bind()).has_table("ownerexplanationsettings"):
        return
    op.create_table(
        "ownerexplanationsettings",
        sa.Column("player_id", sa.String(), sa.ForeignKey("player.id"), primary_key=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("revision", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    settings = sa.table("ownerexplanationsettings", sa.column("player_id", sa.String()))
    if op.get_bind().execute(sa.select(settings.c.player_id).limit(1)).first() is not None:
        raise RuntimeError(
            "Cannot downgrade while local explanation consent is saved; "
            "disable it before downgrading or restore the reviewed pre-migration backup."
        )
    op.drop_table("ownerexplanationsettings")
