from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_05"
down_revision: str | Sequence[str] | None = "20260728_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_POSITION_FOREIGN_KEY = (
    "fk_persistedlessonopportunity_source_position_id_position"
)
_SOURCE_POSITION_INDEX = "ix_persistedlessonopportunity_source_position_id"


def _source_fen(lesson_spec: object, opportunity_id: object) -> str:
    if isinstance(lesson_spec, str):
        lesson_spec = json.loads(lesson_spec)
    if not isinstance(lesson_spec, dict):
        raise RuntimeError(
            f"Persisted lesson opportunity {opportunity_id} has an invalid lesson specification"
        )
    source = lesson_spec.get("source")
    if not isinstance(source, dict):
        raise RuntimeError(
            f"Persisted lesson opportunity {opportunity_id} has no source FEN"
        )
    fen = source.get("fen")
    if not isinstance(fen, str):
        raise RuntimeError(
            f"Persisted lesson opportunity {opportunity_id} has no source FEN"
        )
    return fen


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("persistedlessonopportunity"):
        return

    opportunity_columns = {
        column["name"] for column in inspector.get_columns("persistedlessonopportunity")
    }
    if "source_position_id" not in opportunity_columns:
        with op.batch_alter_table("persistedlessonopportunity") as batch:
            batch.add_column(sa.Column("source_position_id", sa.Uuid(), nullable=True))

    connection = op.get_bind()
    opportunities = connection.execute(
        sa.text(
            "SELECT id, game_id, lesson_spec FROM persistedlessonopportunity "
            "ORDER BY id"
        )
    ).mappings()
    for opportunity in opportunities:
        source_fen = _source_fen(opportunity["lesson_spec"], opportunity["id"])
        position_ids = connection.execute(
            sa.text("SELECT id FROM position WHERE game_id = :game_id AND fen = :fen"),
            {"game_id": opportunity["game_id"], "fen": source_fen},
        ).scalars().all()
        if len(position_ids) != 1:
            raise RuntimeError(
                "Persisted lesson opportunity "
                f"{opportunity['id']} source FEN resolved to {len(position_ids)} positions"
            )
        connection.execute(
            sa.text(
                "UPDATE persistedlessonopportunity "
                "SET source_position_id = :source_position_id WHERE id = :id"
            ),
            {"source_position_id": position_ids[0], "id": opportunity["id"]},
        )

    inspector = sa.inspect(connection)
    foreign_key_names = {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("persistedlessonopportunity")
    }
    index_names = {
        index["name"] for index in inspector.get_indexes("persistedlessonopportunity")
    }
    with op.batch_alter_table("persistedlessonopportunity") as batch:
        if _SOURCE_POSITION_FOREIGN_KEY not in foreign_key_names:
            batch.create_foreign_key(
                _SOURCE_POSITION_FOREIGN_KEY,
                "position",
                ["source_position_id"],
                ["id"],
            )
        if _SOURCE_POSITION_INDEX not in index_names:
            batch.create_index(_SOURCE_POSITION_INDEX, ["source_position_id"])
        batch.alter_column(
            "source_position_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("persistedlessonopportunity"):
        return
    columns = {
        column["name"] for column in inspector.get_columns("persistedlessonopportunity")
    }
    if "source_position_id" not in columns:
        return

    foreign_key_names = {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("persistedlessonopportunity")
    }
    index_names = {
        index["name"] for index in inspector.get_indexes("persistedlessonopportunity")
    }
    with op.batch_alter_table("persistedlessonopportunity") as batch:
        if _SOURCE_POSITION_INDEX in index_names:
            batch.drop_index(_SOURCE_POSITION_INDEX)
        if _SOURCE_POSITION_FOREIGN_KEY in foreign_key_names:
            batch.drop_constraint(_SOURCE_POSITION_FOREIGN_KEY, type_="foreignkey")
        batch.drop_column("source_position_id")
