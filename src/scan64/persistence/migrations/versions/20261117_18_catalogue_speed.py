"""Index catalogue selection and backfill searchable source themes.

Rollback drops only derived metadata and the new selection index. Payloads,
reserved daily sets and ratings are untouched; re-upgrade rebuilds themes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261117_18"
down_revision: str | Sequence[str] | None = "20261113_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "contentitem" not in tables:
        return
    op.create_index(
        "ix_contentitem_domain_difficulty",
        "contentitem",
        ["domain", "difficulty_estimate"],
        if_not_exists=True,
    )
    if "puzzletheme" not in tables:
        op.create_table(
            "puzzletheme",
            sa.Column(
                "item_id",
                sa.String(),
                sa.ForeignKey("contentitem.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("theme", sa.String(), primary_key=True),
        )
    op.create_index(
        "ix_puzzletheme_theme_item", "puzzletheme", ["theme", "item_id"], if_not_exists=True
    )
    content = sa.table(
        "contentitem",
        sa.column("id", sa.String()),
        sa.column("domain", sa.String()),
        sa.column("payload", sa.JSON()),
    )
    themes = sa.table(
        "puzzletheme", sa.column("item_id", sa.String()), sa.column("theme", sa.String())
    )
    # A retry may encounter SQLite DDL from an interrupted migration. Rebuild the
    # projection from its authority, in bounded batches, without changing content.
    connection.execute(themes.delete())
    result = connection.execute(
        sa.select(content.c.id, content.c.payload)
        .where(content.c.domain == "puzzles")
        .execution_options(yield_per=500)
    )
    try:
        for batch in result.partitions(500):
            rows = [
                {"item_id": item_id, "theme": theme}
                for item_id, payload in batch
                if isinstance(payload, dict) and isinstance(payload.get("themes"), list)
                for theme in sorted(
                    {value for value in payload["themes"] if isinstance(value, str) and value}
                )
            ]
            if rows:
                connection.execute(themes.insert(), rows)
    finally:
        result.close()


def downgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "puzzletheme" in tables:
        op.drop_table("puzzletheme")
    if "contentitem" in tables:
        op.drop_index("ix_contentitem_domain_difficulty", table_name="contentitem", if_exists=True)
