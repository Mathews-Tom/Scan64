from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2

from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

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
    habit_tables = {
        "habitpolicy",
        "habitgoal",
        "habitday",
        "habitcredit",
        "patterntarget",
        "patternaward",
        "patternawardevidence",
    }
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
            assert connection.execute(
                text("SELECT COUNT(*) FROM patternawardevidence")
            ).scalar_one() == 5
            assert connection.execute(text("SELECT COUNT(*) FROM game")).scalar_one() == 5
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        if cycle == 0:
            command.downgrade(config, "20261113_16")
            assert not habit_tables & set(inspect(engine).get_table_names())
            with engine.connect() as connection:
                assert connection.execute(text("SELECT COUNT(*) FROM game")).scalar_one() == 5
                assert connection.execute(
                    text("SELECT COUNT(*) FROM analysisjob")
                ).scalar_one() == 5
    source = create_engine(f"sqlite:///{original}")
    assert not habit_tables & set(inspect(source).get_table_names())
    with source.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "20261113_16"
        )
    source.dispose()
    engine.dispose()
