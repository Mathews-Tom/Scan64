from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session

from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.pgn import build_pgn
from scan64.persistence.database import _migration_config, create_database_engine


def test_clock_upgrade_retains_untimed_history_and_backup_across_empty_downgrade(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite"
    engine = create_database_engine(f"sqlite:///{database}")
    config = _migration_config(engine)
    command.upgrade(config, "20261117_18")
    with Session(engine) as session:
        game = Game(pgn="", owner_player_id="legacy-clock-owner", owner_color="white")
        game.pgn = build_pgn(game)
        session.add(game)
        session.flush()
        play = PlaySession(player_id="legacy-clock-owner", game_id=game.id)
        session.add(play)
        session.commit()
        game_id, play_id, original_pgn = game.id, play.id, game.pgn
    backup = tmp_path / "before-m100.sqlite"
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    try:
        for _ in range(2):
            command.upgrade(config, "head")
            with Session(engine) as session:
                retained = session.get(Game, game_id)
                assert retained is not None and retained.pgn == original_pgn
                play = session.get(PlaySession, play_id)
                assert play is not None and play.clock_config is None and play.status == "active"
                assert session.get(PlayClock, play_id) is None
            assert "playclock" in inspect(engine).get_table_names()
            command.downgrade(config, "20261117_18")
            assert "playclock" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        with sqlite3.connect(backup) as retained_backup:
            assert retained_backup.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("20261117_18",)
            assert retained_backup.execute("SELECT COUNT(*) FROM game").fetchone() == (1,)
            assert retained_backup.execute("SELECT COUNT(*) FROM playsession").fetchone() == (1,)
            assert retained_backup.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='playclock'"
            ).fetchone() == (0,)
    finally:
        engine.dispose()


def test_interrupted_clock_upgrade_retains_state_and_refuses_lossy_downgrade(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'timed.sqlite'}")
    config = _migration_config(engine)
    command.upgrade(config, "20261117_18")
    PlayClock.metadata.tables["playclock"].create(engine)
    deadline = datetime.now(UTC) + timedelta(seconds=60)
    with Session(engine) as session:
        game = Game(
            pgn="",
            owner_player_id="timed-clock-owner",
            owner_color="white",
            headers={"TimeControl": "60+2"},
        )
        game.pgn = build_pgn(game)
        session.add(game)
        session.flush()
        play = PlaySession(
            player_id="timed-clock-owner",
            game_id=game.id,
            clock_config={"initial_seconds": "60", "increment_seconds": "2"},
            opponent_config={"player_color": "white"},
        )
        session.add(play)
        session.flush()
        clock = PlayClock(
            session_id=play.id,
            initial_seconds=60,
            increment_seconds=2,
            white_remaining_ms=60000,
            black_remaining_ms=60000,
            active_color="white",
            deadline_at=deadline,
        )
        session.add(clock)
        session.commit()
        play_id = play.id
    try:
        command.upgrade(config, "head")
        assert {index["name"] for index in inspect(engine).get_indexes("playclock")} == {
            "ix_playclock_deadline_at"
        }
        with pytest.raises(RuntimeError, match="authoritative play clocks are retained"):
            command.downgrade(config, "20261117_18")
        with Session(engine) as session:
            retained = session.get(PlayClock, play_id)
            assert retained is not None
            assert retained.white_remaining_ms == retained.black_remaining_ms == 60000
            assert retained.active_color == "white" and retained.increment_seconds == 2
            assert retained.deadline_at is not None
            assert retained.deadline_at.replace(tzinfo=UTC) == deadline
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("20261119_22")
    finally:
        engine.dispose()
