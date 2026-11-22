"""Add owner-owned repertoire trees and verified attempts without rescheduling anything."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_23"
down_revision: str | Sequence[str] | None = "20261119_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    if not sa.inspect(connection).has_table("repertoire"):
        op.create_table(
            "repertoire",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("pgn", sa.String(), nullable=False),
            sa.Column("tree", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("player_id", "id", name="uq_repertoire_owner"),
        )
        op.create_index("ix_repertoire_player_id", "repertoire", ["player_id"])
    if not sa.inspect(connection).has_table("repertoireattempt"):
        op.create_table(
            "repertoireattempt",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("repertoire_id", sa.Uuid(), nullable=False),
            sa.Column("node_id", sa.Integer(), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("submitted_move", sa.String(), nullable=False),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=False),
            sa.Column("verification_status", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "player_id",
                "repertoire_id",
                "node_id",
                "sequence",
                name="uq_repertoire_decision_review",
            ),
            sa.ForeignKeyConstraint(
                ["player_id", "repertoire_id"],
                ["repertoire.player_id", "repertoire.id"],
                name="fk_repertoire_attempt_owner",
            ),
            sa.CheckConstraint("node_id >= 0", name="ck_repertoire_node_id"),
            sa.CheckConstraint(
                "verification_status IN ('verified', 'historical')",
                name="ck_repertoire_verification",
            ),
        )
        op.create_index("ix_repertoireattempt_player_id", "repertoireattempt", ["player_id"])


def downgrade() -> None:
    connection = op.get_bind()
    tables = sa.inspect(connection).get_table_names()
    for table in ("repertoireattempt", "repertoire"):
        if (
            table in tables
            and connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first()
        ):
            raise RuntimeError(
                "Cannot downgrade with imported repertoires or practice present. Retain a "
                "current account export and restore the pre-migration backup on a copy; "
                "downgrade would discard owner content or learned state."
            )
    for table, predicate in (
        ("reviewschedule", "item_id LIKE 'repertoire:%'"),
        ("schedulereviewobservation", "source_kind = 'repertoire'"),
    ):
        if (
            table in tables
            and connection.execute(
                sa.text(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1")
            ).first()
        ):
            raise RuntimeError(
                "Cannot downgrade with repertoire scheduling history; restore a backup copy"
            )
    if "repertoireattempt" in tables:
        op.drop_index("ix_repertoireattempt_player_id", table_name="repertoireattempt")
        op.drop_table("repertoireattempt")
    if "repertoire" in tables:
        op.drop_index("ix_repertoire_player_id", table_name="repertoire")
        op.drop_table("repertoire")
