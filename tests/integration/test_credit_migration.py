from __future__ import annotations

from pathlib import Path
from shutil import copy2
from uuid import uuid4

from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from scan64.api.models import Player
from scan64.chess.analysis.credit import CHECK_SKILL
from scan64.chess.games.models import PlaySession
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import _migration_config
from tests.integration.test_credit_lifecycle import _seed_credit_records


def test_copy_migration_preserves_unknown_sessions_and_downgrade_keeps_alpha(
    tmp_path: Path,
) -> None:
    original = tmp_path / "before-credit.db"
    working = tmp_path / "migration-copy.db"
    engine = create_engine(f"sqlite:///{original}")
    config = _migration_config(engine)
    command.upgrade(config, "20261112_12")
    player_id = "credit-migration-owner"
    old_session_id = uuid4()
    with Session(engine) as session:
        session.add(Player(id=player_id, preferences={"preserved": True}))
        session.add(SkillState(
            player_id=player_id, concept_code="tactics.hanging_piece", alpha=4.25, beta=2.75,
        ))
        session.commit()
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO playsession "
            "(id, player_id, game_id, opponent_config, clock_config, coach_mode, status) "
            "VALUES (:id, :player, NULL, '{}', NULL, 0, 'active')"
        ), {"id": old_session_id.hex, "player": player_id})
    engine.dispose()
    copy2(original, working)
    engine = create_engine(f"sqlite:///{working}")
    config = _migration_config(engine)
    command.upgrade(config, "20261112_13")
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("playsession")}
    assert columns["assistance_tracking_version"]["nullable"] is True
    assert columns["credit_analysis_status"]["nullable"] is True
    assert set(inspector.get_pk_constraint("sessionhintreveal")["constrained_columns"]) == {
        "session_id", "prefix_hash",
    }
    assert set(inspector.get_pk_constraint("positivemovecredit")["constrained_columns"]) == {
        "player_id", "game_id", "canonical_id", "skill_id",
    }
    assert len(inspector.get_foreign_keys("sessionhintreveal")) == 1
    assert len(inspector.get_foreign_keys("positivemovecredit")) == 3
    with Session(engine) as session:
        old = session.get(PlaySession, old_session_id)
        assert old is not None
        assert old.assistance_tracking_version is None
        assert old.credit_analysis_status is None
        _seed_credit_records(session, player_id)
        awarded = session.get(SkillState, (player_id, CHECK_SKILL))
        assert awarded is not None and awarded.alpha == 3.0
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM positivemovecredit")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM sessionhintreveal")).scalar_one() == 1
    command.downgrade(config, "20261112_12")
    inspector = inspect(engine)
    assert "positivemovecredit" not in inspector.get_table_names()
    assert "sessionhintreveal" not in inspector.get_table_names()
    assert "assistance_tracking_version" not in {
        column["name"] for column in inspector.get_columns("playsession")
    }
    assert "credit_analysis_status" not in {
        column["name"] for column in inspector.get_columns("playsession")
    }
    with Session(engine) as session:
        original_state = session.get(SkillState, (player_id, "tactics.hanging_piece"))
        awarded = session.get(SkillState, (player_id, CHECK_SKILL))
        assert original_state is not None
        assert (original_state.alpha, original_state.beta) == (4.25, 2.75)
        assert awarded is not None and awarded.alpha == 3.0
        assert session.get(Player, player_id).preferences == {"preserved": True}
    command.upgrade(config, "20261112_13")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM positivemovecredit")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM sessionhintreveal")).scalar_one() == 0
        assert connection.execute(text(
            "SELECT COUNT(*) FROM playsession WHERE assistance_tracking_version IS NOT NULL"
        )).scalar_one() == 0
    backup_engine = create_engine(f"sqlite:///{original}")
    with backup_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "20261112_12"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM playsession")).scalar_one() == 1
    assert "positivemovecredit" not in inspect(backup_engine).get_table_names()
    backup_engine.dispose()
    engine.dispose()
