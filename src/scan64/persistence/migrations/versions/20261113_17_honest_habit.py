"""Prospective habit evidence; downgrade never changes learning or ratings."""
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20261113_17"
down_revision: str | Sequence[str] | None = "20261113_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    # SQLite may retain earlier DDL after a failed upgrade. Complete every
    # missing table on retry without replacing existing prospective cutovers.
    if "habitpolicy" not in existing:
        op.create_table(
            "habitpolicy",
            sa.Column("player_id", sa.String(), primary_key=True),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("effective_at", sa.DateTime(), nullable=False),
        )
    if "player" in existing:
        op.get_bind().execute(
            sa.text(
                "INSERT INTO habitpolicy (player_id, policy_version, effective_at) "
                "SELECT id, :policy, :cutover FROM player WHERE true "
                "ON CONFLICT (player_id) DO NOTHING"
            ),
            {"policy": "habit-v1", "cutover": datetime.now(UTC)},
        )
    if "habitgoal" not in existing:
        op.create_table(
            "habitgoal",
            sa.Column("player_id", sa.String(), primary_key=True),
            sa.Column("effective_day", sa.String(), primary_key=True),
            sa.Column("goal", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "habitday" not in existing:
        op.create_table(
            "habitday",
            sa.Column("player_id", sa.String(), primary_key=True),
            sa.Column("utc_day", sa.String(), primary_key=True),
            sa.Column("goal", sa.Integer(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("met_at", sa.DateTime(), nullable=True),
        )
    if "habitcredit" not in existing:
        op.create_table(
            "habitcredit",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("utc_day", sa.String(), nullable=False),
            sa.Column("source_kind", sa.String(), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("source_key", sa.String(), nullable=False),
            sa.Column("source_epd", sa.String(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("trust_snapshot", sa.JSON(), nullable=True),
            sa.UniqueConstraint("player_id", "utc_day", "source_key"),
            sa.UniqueConstraint("player_id", "utc_day", "source_epd"),
            sa.UniqueConstraint("player_id", "source_kind", "source_id"),
        )
    op.create_index(
        "ix_habitcredit_player_id", "habitcredit", ["player_id"], if_not_exists=True,
    )
    if "patterntarget" not in existing:
        op.create_table(
            "patterntarget",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("pattern_snapshot", sa.JSON(), nullable=True),
            sa.Column("practice", sa.JSON(), nullable=True),
            sa.Column("practice_completed_at", sa.DateTime(), nullable=True),
            sa.Column("window_game_ids", sa.JSON(), nullable=True),
            sa.Column("invalidated_at", sa.DateTime(), nullable=True),
            sa.Column("invalidation_reason", sa.String(), nullable=True),
            sa.UniqueConstraint("player_id", "code", "policy_version"),
        )
    op.create_index(
        "ix_patterntarget_player_id", "patterntarget", ["player_id"], if_not_exists=True,
    )
    if "patternaward" not in existing:
        op.create_table(
            "patternaward",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), sa.ForeignKey("patterntarget.id"), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("window_start", sa.String(), nullable=False),
            sa.Column("game_ids", sa.JSON(), nullable=True),
            sa.Column("awarded_at", sa.DateTime(), nullable=False),
            sa.Column("barrier_game_id", sa.Uuid(), sa.ForeignKey("game.id"), nullable=True),
            sa.Column(
                "barrier_analysis_job_id", sa.Uuid(), sa.ForeignKey("analysisjob.id"), nullable=True,
            ),
            sa.UniqueConstraint("player_id", "code", "policy_version", "window_start"),
            sa.UniqueConstraint("target_id"),
        )
    op.create_index(
        "ix_patternaward_player_id", "patternaward", ["player_id"], if_not_exists=True,
    )
    op.create_index(
        "ix_patternaward_target_id", "patternaward", ["target_id"], if_not_exists=True,
    )
    if "patternawardevidence" not in existing:
        op.create_table(
            "patternawardevidence",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("player_id", sa.String(), nullable=False),
            sa.Column("award_id", sa.String(), sa.ForeignKey("patternaward.id"), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("game_id", sa.Uuid(), sa.ForeignKey("game.id"), nullable=False),
            sa.Column("analysis_job_id", sa.Uuid(), sa.ForeignKey("analysisjob.id"), nullable=False),
            sa.Column("game_created_at", sa.DateTime(), nullable=False),
            sa.Column("job_completed_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("award_id", "ordinal"),
            sa.UniqueConstraint("award_id", "game_id"),
            sa.CheckConstraint("ordinal >= 1 AND ordinal <= 5"),
        )
    op.create_index(
        "ix_patternawardevidence_player_id", "patternawardevidence", ["player_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_patternawardevidence_award_id", "patternawardevidence", ["award_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    for table in ("patternawardevidence", "patternaward", "patterntarget", "habitcredit", "habitday", "habitgoal", "habitpolicy"):
        op.drop_table(table)
