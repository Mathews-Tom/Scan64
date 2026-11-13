from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import copy2

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from scan64.api.models import Player, PlayerProfile
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.models import Game
from scan64.content.puzzle_practice_models import PuzzleRating
from scan64.learning.habit_models import (
    HabitDay,
    HabitPolicy,
    PatternAward,
    PatternAwardEvidence,
    PatternTarget,
)
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import _migration_config

HABIT_TABLES = (
    "habitpolicy",
    "habitgoal",
    "habitday",
    "habitcredit",
    "patterntarget",
    "patternaward",
    "patternawardevidence",
)


def test_habit_populated_copy_up_down_up_preserves_learning_and_starts_prospectively(
    tmp_path: Path,
) -> None:
    original, copied = tmp_path / "m67-original.db", tmp_path / "m69-copy.db"
    engine = create_engine(f"sqlite:///{original}")
    config = _migration_config(engine)
    command.upgrade(config, "20261113_16")
    with Session(engine) as session:
        session.add_all(
            [
                Player(id="migration-habit-owner", preferences={"manual_bot_level": 4}),
                PlayerProfile(player_id="migration-habit-owner", rating=1632),
                PuzzleRating(player_id="migration-habit-owner", rating=1264, rated_count=4),
                SkillState(
                    player_id="migration-habit-owner", concept_code="example", alpha=4, beta=3
                ),
            ]
        )
        session.commit()
        games = [Game(pgn="", moves=[], owner_player_id="migration-habit-owner") for _ in range(5)]
        jobs = [
            AnalysisJob(game_id=game.id, status="completed", completed_at=datetime.now(UTC))
            for game in games
        ]
        session.add_all([*games, *jobs])
        references = [
            (game.id, game.created_at, job.id, job.completed_at)
            for game, job in zip(games, jobs, strict=True)
        ]
        session.commit()
    engine.dispose()
    copy2(original, copied)
    engine = create_engine(f"sqlite:///{copied}")
    config = _migration_config(engine)
    habit_tables = set(HABIT_TABLES)
    for cycle in range(2):
        before = datetime.now(UTC)
        command.upgrade(config, "head")
        assert habit_tables <= set(inspect(engine).get_table_names())
        inspector = inspect(engine)
        assert {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("patternawardevidence")
        } == {("award_id", "ordinal"), ("award_id", "game_id")}
        assert {
            tuple(item["constrained_columns"]): item["referred_table"]
            for item in inspector.get_foreign_keys("patternawardevidence")
        } == {
            ("award_id",): "patternaward",
            ("game_id",): "game",
            ("analysis_job_id",): "analysisjob",
        }
        assert any(
            "ordinal >= 1 AND ordinal <= 5" in item["sqltext"]
            for item in inspector.get_check_constraints("patternawardevidence")
        )
        assert {
            tuple(item["constrained_columns"]): item["referred_table"]
            for item in inspector.get_foreign_keys("patternaward")
        } == {
            ("target_id",): "patterntarget",
            ("barrier_game_id",): "game",
            ("barrier_analysis_job_id",): "analysisjob",
        }
        with Session(engine) as session:
            policy = session.get(HabitPolicy, "migration-habit-owner")
            assert policy is not None and policy.policy_version == "habit-v1"
            assert policy.effective_at.replace(tzinfo=UTC) >= before
            assert session.get(PlayerProfile, "migration-habit-owner").rating == 1632
            assert session.get(PuzzleRating, "migration-habit-owner").rating == 1264
            skill = session.get(SkillState, ("migration-habit-owner", "example"))
            assert (skill.alpha, skill.beta) == (4, 3)
            assert session.get(Player, "migration-habit-owner").preferences == {
                "manual_bot_level": 4
            }
            assert (
                session.get(HabitDay, ("migration-habit-owner", before.date().isoformat())) is None
            )
            target = PatternTarget(player_id="migration-habit-owner", code="example")
            session.add(target)
            session.flush()
            award = PatternAward(
                player_id=target.player_id,
                target_id=target.id,
                code=target.code,
                window_start=str(references[0][0]),
                game_ids=[str(row[0]) for row in references],
                awarded_at=datetime.now(UTC),
            )
            session.add(award)
            session.flush()
            for ordinal, (game_id, created_at, job_id, completed_at) in enumerate(references, 1):
                assert completed_at is not None
                session.add(
                    PatternAwardEvidence(
                        player_id=target.player_id,
                        award_id=award.id,
                        ordinal=ordinal,
                        game_id=game_id,
                        analysis_job_id=job_id,
                        game_created_at=created_at,
                        job_completed_at=completed_at,
                    )
                )
            session.commit()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM habitcredit")).scalar_one() == 0
            assert (
                connection.execute(text("SELECT COUNT(*) FROM patternawardevidence")).scalar_one()
                == 5
            )
            assert connection.execute(text("SELECT COUNT(*) FROM game")).scalar_one() == 5
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        if cycle == 0:
            command.downgrade(config, "20261113_16")
            assert not habit_tables & set(inspect(engine).get_table_names())
            with engine.connect() as connection:
                assert connection.execute(text("SELECT COUNT(*) FROM game")).scalar_one() == 5
                assert (
                    connection.execute(text("SELECT COUNT(*) FROM analysisjob")).scalar_one() == 5
                )
    source = create_engine(f"sqlite:///{original}")
    assert not habit_tables & set(inspect(source).get_table_names())
    with source.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "20261113_16"
        )
    source.dispose()
    engine.dispose()


@pytest.mark.parametrize("existing_count", range(1, len(HABIT_TABLES) + 1))
def test_habit_migration_retry_completes_partial_schema_without_changing_cutover(
    tmp_path: Path,
    existing_count: int,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    config = _migration_config(engine)
    command.upgrade(config, "20261113_16")
    with Session(engine) as session:
        session.add_all([Player(id="existing-cutover"), Player(id="missing-cutover")])
        session.commit()
    for name in HABIT_TABLES[:existing_count]:
        SQLModel.metadata.tables[name].create(engine)
    preserved = datetime.now(UTC) - timedelta(days=2)
    with Session(engine) as session:
        session.add(HabitPolicy(player_id="existing-cutover", effective_at=preserved))
        if existing_count >= 3:
            session.add(
                HabitDay(
                    player_id="existing-cutover",
                    utc_day=preserved.date().isoformat(),
                    goal=7,
                )
            )
        session.commit()
    if existing_count >= 4:
        # A failed index DDL must not be hidden by the table's presence either.
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_habitcredit_player_id"))
    before = datetime.now(UTC)
    command.upgrade(config, "head")
    assert set(HABIT_TABLES) <= set(inspect(engine).get_table_names())
    assert "ix_habitcredit_player_id" in {
        row["name"] for row in inspect(engine).get_indexes("habitcredit")
    }
    with Session(engine) as session:
        existing = session.get(HabitPolicy, "existing-cutover")
        missing = session.get(HabitPolicy, "missing-cutover")
        assert existing is not None and missing is not None
        assert existing.effective_at.replace(tzinfo=UTC) == preserved
        added_cutover = missing.effective_at.replace(tzinfo=UTC)
        assert added_cutover >= before
        if existing_count >= 3:
            day = session.get(HabitDay, ("existing-cutover", preserved.date().isoformat()))
            assert day is not None and day.goal == 7
    # All DDL may have succeeded before a failed revision-marker commit.
    command.stamp(config, "20261113_16")
    command.upgrade(config, "head")
    with Session(engine) as session:
        existing = session.get(HabitPolicy, "existing-cutover")
        missing = session.get(HabitPolicy, "missing-cutover")
        assert existing is not None and missing is not None
        assert existing.effective_at.replace(tzinfo=UTC) == preserved
        assert missing.effective_at.replace(tzinfo=UTC) == added_cutover
        assert session.exec(text("SELECT COUNT(*) FROM habitpolicy")).scalar_one() == 2
    engine.dispose()
