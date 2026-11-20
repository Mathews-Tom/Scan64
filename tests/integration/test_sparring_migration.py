from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session, select

from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview
from scan64.chess.analysis.review import stage_game_review
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.learning.sparring import SparringAttempt
from scan64.persistence.database import _migration_config, create_database_engine, migrate_database


def test_upgrade_and_unused_downgrade_preserve_owned_source_review(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'source.sqlite'}")
    config = _migration_config(engine)
    try:
        migrate_database(engine)
        command.downgrade(config, "20261117_18")
        with Session(engine) as session:
            game = Game(pgn="1. e4 *", moves=["e2e4"], owner_player_id="owner", owner_color="white")
            positions: dict[str, Position] = {}
            analyses = {}
            rows = stage_game_review(
                game,
                [
                    EngineAnalysis(raw_result=[{"score_cp": 350, "pv": ["e2e4"]}]),
                    EngineAnalysis(raw_result=[{"score_cp": -350, "pv": ["e7e5"]}]),
                ],
                positions,
                analyses,
                set(),
            )
            session.add(game)
            session.flush()
            session.add_all(positions.values())
            session.flush()
            session.add_all(analyses.values())
            session.flush()
            session.add_all(rows)
            session.commit()
            session.refresh(game)
            session.refresh(rows[0])
            original = game.model_dump(mode="json")
            review = rows[0].model_dump(mode="json")
        command.upgrade(config, "head")
        with Session(engine) as session:
            restored = session.exec(select(Game)).one()
            restored_review = session.exec(select(GameMoveReview)).one()
            assert restored.model_dump(mode="json") == original
            assert restored_review.model_dump(mode="json") == review
            assert session.exec(select(SparringAttempt)).all() == []
        foreign_keys = inspect(engine).get_foreign_keys("sparringattempt")
        assert {key["referred_table"] for key in foreign_keys} == {
            "gamemovereview",
            "position",
            "engineanalysis",
            "playsession",
        }
        command.downgrade(config, "20261117_18")
        assert "sparringattempt" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            assert session.exec(select(Game)).one().model_dump(mode="json") == original
            assert session.exec(select(GameMoveReview)).one().model_dump(mode="json") == review
    finally:
        engine.dispose()


@pytest.mark.parametrize("target_revision", ["20261119_19", "20261117_18"])
def test_downgrade_refuses_to_discard_even_active_sparring_history(
    tmp_path: Path, target_revision: str
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'used.sqlite'}")
    try:
        migrate_database(engine)
        with Session(engine) as session:
            game = Game(pgn="1. e4 *", moves=["e2e4"], owner_player_id="owner", owner_color="white")
            positions: dict[str, Position] = {}
            analyses = {}
            rows = stage_game_review(
                game,
                [
                    EngineAnalysis(raw_result=[{"score_cp": 350, "pv": ["e2e4"]}]),
                    EngineAnalysis(raw_result=[{"score_cp": -350, "pv": ["e7e5"]}]),
                ],
                positions,
                analyses,
                set(),
            )
            session.add(game)
            session.flush()
            session.add_all(positions.values())
            session.flush()
            session.add_all(analyses.values())
            session.flush()
            session.add_all(rows)
            session.flush()
            play = PlaySession(player_id="owner", game_id=game.id)
            session.add(play)
            session.flush()
            attempt = SparringAttempt(
                player_id="owner",
                source_review_id=rows[0].id,
                source_position_id=rows[0].source_position_id,
                source_analysis_id=rows[0].before_analysis_id,
                play_session_id=play.id,
                source_ply=0,
            )
            session.add(attempt)
            session.commit()
            attempt_id = attempt.id
            review_id = rows[0].id
        with pytest.raises(RuntimeError, match="Cannot downgrade with sparring attempts present"):
            command.downgrade(_migration_config(engine), target_revision)
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "20261119_20"
            )
        with Session(engine) as session:
            assert session.get(SparringAttempt, attempt_id) is not None
            assert session.exec(select(GameMoveReview)).one().id == review_id
    finally:
        engine.dispose()
