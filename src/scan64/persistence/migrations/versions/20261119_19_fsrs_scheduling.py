"""Introduce FSRS without rescheduling or fabricating historical reviews.

Unused conversions downgrade losslessly. Once FSRS reviews exist, restore the
pre-migration backup instead of silently discarding learned state and observations.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_19"
down_revision: str | Sequence[str] | None = "20261117_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    if "reviewschedule" not in sa.inspect(connection).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(connection).get_columns("reviewschedule")}
    with op.batch_alter_table("reviewschedule") as batch:
        for old, new in (
            ("interval_days", "legacy_interval_days"),
            ("ease_factor", "legacy_ease_factor"),
        ):
            if old in columns:
                if new in columns:
                    raise RuntimeError("Ambiguous legacy and FSRS schedule columns")
                batch.alter_column(
                    old, new_column_name=new, existing_type=sa.Float(), nullable=True
                )
        if "fsrs_state" not in columns:
            batch.add_column(
                sa.Column("fsrs_state", sa.Integer(), nullable=False, server_default="1")
            )
        if "fsrs_step" not in columns:
            batch.add_column(
                sa.Column("fsrs_step", sa.Integer(), nullable=True, server_default="0")
            )
        if "fsrs_stability" not in columns:
            batch.add_column(sa.Column("fsrs_stability", sa.Float(), nullable=True))
        if "fsrs_difficulty" not in columns:
            batch.add_column(sa.Column("fsrs_difficulty", sa.Float(), nullable=True))
    # Dates, ownership, retirement and legacy values are deliberately untouched.
    if "schedulereviewobservation" not in sa.inspect(connection).get_table_names():
        op.create_table(
            "schedulereviewobservation",
            sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("item_id", sa.String(), nullable=False),
            sa.Column("source_kind", sa.String(), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("hints_used", sa.Integer(), nullable=True),
            sa.Column("elapsed_ms", sa.Integer(), nullable=True),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(), nullable=False),
            sa.Column("input_provenance", sa.String(), nullable=False),
            sa.Column(
                "verification_status", sa.String(), nullable=False, server_default="verified"
            ),
            sa.ForeignKeyConstraint(
                ["player_id", "item_id"],
                ["reviewschedule.player_id", "reviewschedule.item_id"],
                name="fk_schedule_review_observation",
            ),
            sa.UniqueConstraint("source_kind", "source_id", name="uq_schedule_review_source"),
        )
    op.create_index(
        "ix_schedulereviewobservation_player_id",
        "schedulereviewobservation",
        ["player_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    if "reviewschedule" not in sa.inspect(connection).get_table_names():
        return
    observations = sa.table("schedulereviewobservation", sa.column("id", sa.Uuid()))
    schedules = sa.table(
        "reviewschedule",
        sa.column("fsrs_stability", sa.Float()),
        sa.column("fsrs_difficulty", sa.Float()),
        sa.column("fsrs_state", sa.Integer()),
        sa.column("fsrs_step", sa.Integer()),
        sa.column("legacy_interval_days", sa.Float()),
        sa.column("legacy_ease_factor", sa.Float()),
    )
    has_observations = connection.scalar(sa.select(observations.c.id).limit(1)) is not None
    has_learned_state = (
        connection.scalar(
            sa.select(sa.literal(1))
            .select_from(schedules)
            .where(
                sa.or_(
                    schedules.c.fsrs_stability.is_not(None),
                    schedules.c.fsrs_difficulty.is_not(None),
                    schedules.c.fsrs_state != 1,
                    schedules.c.fsrs_step.is_(None),
                    schedules.c.fsrs_step != 0,
                )
            )
            .limit(1)
        )
        is not None
    )
    if has_observations or has_learned_state:
        raise RuntimeError(
            "FSRS reviews exist; downgrade would discard learned state or observations. "
            "Retain a current export and restore the pre-migration backup instead."
        )
    op.drop_index("ix_schedulereviewobservation_player_id", table_name="schedulereviewobservation")
    op.drop_table("schedulereviewobservation")
    # New, unreviewed rows get the old constructor defaults, not invented grades.
    connection.execute(
        schedules.update().values(
            legacy_interval_days=sa.func.coalesce(schedules.c.legacy_interval_days, 1.0),
            legacy_ease_factor=sa.func.coalesce(schedules.c.legacy_ease_factor, 2.5),
        )
    )
    with op.batch_alter_table("reviewschedule") as batch:
        for column in ("fsrs_state", "fsrs_step", "fsrs_stability", "fsrs_difficulty"):
            batch.drop_column(column)
        batch.alter_column(
            "legacy_interval_days",
            new_column_name="interval_days",
            existing_type=sa.Float(),
            nullable=False,
        )
        batch.alter_column(
            "legacy_ease_factor",
            new_column_name="ease_factor",
            existing_type=sa.Float(),
            nullable=False,
        )
