from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_02"
down_revision: str | Sequence[str] | None = "20260727_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    game_columns = {column["name"] for column in inspector.get_columns("game")}
    game_indexes = {index["name"] for index in inspector.get_indexes("game")}
    with op.batch_alter_table("game") as batch:
        if "owner_player_id" not in game_columns:
            batch.add_column(sa.Column("owner_player_id", sa.String(), nullable=True))
        if "ix_game_owner_player_id" not in game_indexes:
            batch.create_index("ix_game_owner_player_id", ["owner_player_id"])

    opportunity_columns = {
        column["name"] for column in inspector.get_columns("persistedlessonopportunity")
    }
    opportunity_indexes = {
        index["name"] for index in inspector.get_indexes("persistedlessonopportunity")
    }
    with op.batch_alter_table("persistedlessonopportunity") as batch:
        if "player_id" not in opportunity_columns:
            batch.add_column(sa.Column("player_id", sa.String(), nullable=True))
        if "ix_persistedlessonopportunity_player_id" not in opportunity_indexes:
            batch.create_index("ix_persistedlessonopportunity_player_id", ["player_id"])


def downgrade() -> None:
    with op.batch_alter_table("persistedlessonopportunity") as batch:
        batch.drop_index("ix_persistedlessonopportunity_player_id")
        batch.drop_column("player_id")

    with op.batch_alter_table("game") as batch:
        batch.drop_index("ix_game_owner_player_id")
        batch.drop_column("owner_player_id")
