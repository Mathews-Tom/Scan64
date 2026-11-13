from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_14"
down_revision: str | Sequence[str] | None = "20261112_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if all(
        sa.inspect(op.get_bind()).has_table(table)
        for table in (
            "puzzlerating",
            "puzzledailyset",
            "puzzledailyslot",
            "puzzleratingevent",
        )
    ):
        return
    op.create_table(
        "puzzlerating",
        sa.Column("player_id", sa.String(), sa.ForeignKey("player.id"), primary_key=True),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("rated_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "puzzledailyset",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("player_id", sa.String(), sa.ForeignKey("player.id"), nullable=False),
        sa.Column("utc_day", sa.String(), nullable=False),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("theme_map_version", sa.String(), nullable=False),
        sa.Column("player_rating", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("player_id", "utc_day"),
    )
    op.create_index("ix_puzzledailyset_player_id", "puzzledailyset", ["player_id"])
    op.create_table(
        "puzzledailyslot",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("set_id", sa.String(), sa.ForeignKey("puzzledailyset.id"), nullable=False),
        sa.Column("player_id", sa.String(), sa.ForeignKey("player.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(), sa.ForeignKey("contentitem.id"), nullable=False),
        sa.Column("item_rating", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("widening", sa.String(), nullable=False),
        sa.Column("first_exposure", sa.Boolean(), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("puzzlerun.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("set_id", "position"),
        sa.UniqueConstraint("set_id", "item_id"),
        sa.UniqueConstraint("run_id"),
    )
    for column in ("set_id", "player_id", "item_id"):
        op.create_index(f"ix_puzzledailyslot_{column}", "puzzledailyslot", [column])
    op.create_table(
        "puzzleratingevent",
        sa.Column("run_id", sa.String(), sa.ForeignKey("puzzlerun.id"), primary_key=True),
        sa.Column("slot_id", sa.String(), sa.ForeignKey("puzzledailyslot.id"), nullable=False),
        sa.Column("player_id", sa.String(), sa.ForeignKey("player.id"), nullable=False),
        sa.Column("item_id", sa.String(), sa.ForeignKey("contentitem.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("item_rating", sa.Float(), nullable=False),
        sa.Column("before", sa.Float(), nullable=False),
        sa.Column("after", sa.Float(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("player_id", "sequence"),
        sa.UniqueConstraint("slot_id"),
    )
    op.create_index("ix_puzzleratingevent_player_id", "puzzleratingevent", ["player_id"])


def downgrade() -> None:
    # Export player archives before downgrade; no historical awards are recomputed.
    for table in ("puzzleratingevent", "puzzledailyslot", "puzzledailyset", "puzzlerating"):
        op.drop_table(table)
