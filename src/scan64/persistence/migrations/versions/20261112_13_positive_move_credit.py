from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_13"
down_revision: str | Sequence[str] | None = "20261112_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("playsession"):
        columns = {column["name"] for column in inspector.get_columns("playsession")}
        with op.batch_alter_table("playsession") as batch_op:
            if "assistance_tracking_version" not in columns:
                batch_op.add_column(
                    sa.Column("assistance_tracking_version", sa.Integer(), nullable=True)
                )
            if "credit_analysis_status" not in columns:
                batch_op.add_column(sa.Column("credit_analysis_status", sa.String(), nullable=True))

    if not sa.inspect(op.get_bind()).has_table("sessionhintreveal"):
        op.create_table(
            "sessionhintreveal",
            sa.Column("session_id", sa.Uuid(), nullable=False),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("prefix_hash", sa.String(), nullable=False),
            sa.Column("canonical_id", sa.String(), nullable=False),
            sa.Column("ply", sa.Integer(), nullable=False),
            sa.Column("initial_fen", sa.String(), nullable=False),
            sa.Column("moves", sa.JSON(), nullable=False),
            sa.Column("highest_step", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["playsession.id"]),
            sa.PrimaryKeyConstraint("session_id", "prefix_hash"),
        )
        op.create_index("ix_sessionhintreveal_player_id", "sessionhintreveal", ["player_id"])

    if not sa.inspect(op.get_bind()).has_table("positivemovecredit"):
        op.create_table(
            "positivemovecredit",
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("game_id", sa.Uuid(), nullable=False),
            sa.Column("canonical_id", sa.String(), nullable=False),
            sa.Column("skill_id", sa.String(), nullable=False),
            sa.Column("ply", sa.Integer(), nullable=False),
            sa.Column("source_analysis_id", sa.Uuid(), nullable=False),
            sa.Column("reached_analysis_id", sa.Uuid(), nullable=False),
            sa.Column("proof", sa.JSON(), nullable=False),
            sa.Column("proof_version", sa.Integer(), nullable=False),
            sa.Column("credited_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["game_id"], ["game.id"]),
            sa.ForeignKeyConstraint(["source_analysis_id"], ["engineanalysis.id"]),
            sa.ForeignKeyConstraint(["reached_analysis_id"], ["engineanalysis.id"]),
            sa.PrimaryKeyConstraint("player_id", "game_id", "canonical_id", "skill_id"),
        )


def downgrade() -> None:
    # Export the ledger first. Awarded alpha is deliberately retained: decay makes
    # subtracting historical awards unsafe, and rollback must not guess at it.
    op.drop_table("positivemovecredit")
    op.drop_table("sessionhintreveal")
    if sa.inspect(op.get_bind()).has_table("playsession"):
        with op.batch_alter_table("playsession") as batch_op:
            batch_op.drop_column("credit_analysis_status")
            batch_op.drop_column("assistance_tracking_version")
