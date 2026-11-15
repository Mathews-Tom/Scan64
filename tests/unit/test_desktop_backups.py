from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

from scan64 import desktop_backups


def create_database(data_dir: Path) -> Path:
    database = data_dir / "scan64.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE player (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO player VALUES (1, 'Saved player')")
        connection.commit()
    return database


def snapshots(data_dir: Path) -> list[Path]:
    return sorted((data_dir / "backups").glob(desktop_backups.BACKUP_PATTERN))


def test_first_start_records_version_only_after_success_even_with_existing_database(
    tmp_path: Path,
) -> None:
    create_database(tmp_path)
    startup = desktop_backups.prepare_desktop_backup(tmp_path, "0.6.1")
    marker = tmp_path / desktop_backups.VERSION_MARKER
    assert not marker.exists()
    assert not (tmp_path / "backups").exists()
    startup.record_successful_startup()
    assert marker.read_text(encoding="utf-8") == "0.6.1\n"
    assert snapshots(tmp_path) == []
    assert not list(tmp_path.glob(".desktop-app-version-*"))
    if os.name != "nt":
        assert marker.stat().st_mode & 0o777 == 0o600


def test_newer_version_backs_up_committed_wal_pages_before_advancing_marker(tmp_path: Path) -> None:
    database = create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    with closing(sqlite3.connect(database)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO player VALUES (2, 'Committed WAL player')")
        writer.commit()
        writer.execute("INSERT INTO player VALUES (3, 'Uncommitted player')")
        startup = desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
        assert marker.read_text() == "0.6.1\n"
        [snapshot] = snapshots(tmp_path)
        with closing(sqlite3.connect(snapshot)) as saved:
            assert saved.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert saved.execute("SELECT name FROM player ORDER BY id").fetchall() == [
                ("Saved player",),
                ("Committed WAL player",),
            ]
        writer.rollback()
    startup.record_successful_startup()
    assert marker.read_text() == "0.6.2\n"
    assert snapshot.name.startswith("scan64-")
    assert "-before-0.6.2-" in snapshot.name
    assert snapshot.suffix == ".db"
    assert not list(snapshot.parent.glob("*.partial"))
    if os.name != "nt":
        assert snapshot.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ("0.6.1", "0.6.1"),
        ("0.6.2", "0.6.1"),
        ("0.10.0", "0.9.9"),
        ("1.0.0", "1.0.0-rc.1"),
        ("1.0.0-rc.10", "1.0.0-rc.2"),
        ("1.0.0+first", "1.0.0+second"),
    ],
)
def test_same_or_older_version_never_backs_up_or_lowers_version_marker(
    tmp_path: Path, previous: str, current: str
) -> None:
    create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text(f"{previous}\n", encoding="utf-8")
    desktop_backups.prepare_desktop_backup(tmp_path, current).record_successful_startup()
    assert snapshots(tmp_path) == []
    assert marker.read_text() == f"{previous}\n"


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ("0.9.9", "0.10.0"),
        ("1.0.0-rc.2", "1.0.0-rc.10"),
        ("1.0.0-rc.1", "1.0.0"),
        ("1.0.0-alpha", "1.0.0-alpha.1"),
        ("1.0.0-9", "1.0.0-alpha"),
    ],
)
def test_version_advances_use_semantic_order_not_text_order(
    tmp_path: Path, previous: str, current: str
) -> None:
    create_database(tmp_path)
    (tmp_path / desktop_backups.VERSION_MARKER).write_text(previous, encoding="utf-8")
    desktop_backups.prepare_desktop_backup(tmp_path, current).record_successful_startup()
    assert len(snapshots(tmp_path)) == 1
    assert (tmp_path / desktop_backups.VERSION_MARKER).read_text() == f"{current}\n"


def test_retention_keeps_newest_three_and_never_removes_unrelated_files(tmp_path: Path) -> None:
    create_database(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    unrelated = backup_dir / "manual-export.db"
    unrelated.write_bytes(b"user-managed backup")
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.0", encoding="utf-8")
    for minor in range(1, 6):
        desktop_backups.prepare_desktop_backup(tmp_path, f"0.6.{minor}").record_successful_startup()
    retained = snapshots(tmp_path)
    assert len(retained) == 3
    assert all(f"-before-0.6.{minor}-" in path.name for minor, path in zip(range(3, 6), retained))
    assert unrelated.read_bytes() == b"user-managed backup"
    assert marker.read_text() == "0.6.5\n"


def test_failed_backup_leaves_marker_and_existing_backups_for_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    previous_snapshots = snapshots(tmp_path)
    connect = desktop_backups.sqlite3.connect
    failure = Mock(side_effect=sqlite3.OperationalError("disk full"))
    monkeypatch.setattr(desktop_backups.sqlite3, "connect", failure)
    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    assert marker.read_text() == "0.6.1\n"
    assert snapshots(tmp_path) == previous_snapshots
    assert not list((tmp_path / "backups").glob("*.partial"))
    assert database.is_file()
    monkeypatch.setattr(desktop_backups.sqlite3, "connect", connect)
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2").record_successful_startup()
    assert len(snapshots(tmp_path)) == 2
    assert marker.read_text() == "0.6.2\n"


def test_failed_migration_does_not_commit_marker_and_next_launch_backs_up_again(
    tmp_path: Path,
) -> None:
    create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    # Migration failure never calls record_successful_startup.
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2").record_successful_startup()
    assert len(snapshots(tmp_path)) == 2
    assert marker.read_text() == "0.6.2\n"


def test_failed_marker_replace_keeps_old_marker_and_retries_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    startup = desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    replace = Path.replace
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("marker replace failed")))
    with pytest.raises(OSError, match="marker replace failed"):
        startup.record_successful_startup()
    assert marker.read_text() == "0.6.1\n"
    assert not list(tmp_path.glob(".desktop-app-version-*"))
    monkeypatch.setattr(Path, "replace", replace)
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2").record_successful_startup()
    assert len(snapshots(tmp_path)) == 2
    assert marker.read_text() == "0.6.2\n"


def test_missing_database_is_not_created_by_backup(tmp_path: Path) -> None:
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1", encoding="utf-8")
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2").record_successful_startup()
    assert not (tmp_path / "scan64.db").exists()
    assert snapshots(tmp_path) == []
    assert marker.read_text() == "0.6.2\n"


@pytest.mark.parametrize("invalid", ["", "1", "01.2.3", "1.2.3-01", "v1.2.3", "../1.2.3"])
def test_invalid_versions_fail_without_accepting_unsafe_paths(tmp_path: Path, invalid: str) -> None:
    with pytest.raises(ValueError, match="version"):
        desktop_backups.prepare_desktop_backup(tmp_path, invalid)
    assert list(tmp_path.iterdir()) == []


def test_corrupt_marker_fails_closed_instead_of_treating_upgrade_as_first_run(
    tmp_path: Path,
) -> None:
    create_database(tmp_path)
    (tmp_path / desktop_backups.VERSION_MARKER).write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    assert snapshots(tmp_path) == []


def test_shell_version_takes_precedence_over_reused_server_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = Mock(return_value="0.6.1")
    monkeypatch.setattr(desktop_backups, "version", installed)
    assert desktop_backups.application_version({"SCAN64_APP_VERSION": "0.6.2"}) == "0.6.2"
    installed.assert_not_called()
    assert desktop_backups.application_version({}) == "0.6.1"
    installed.assert_called_once_with("scan64")


def test_empty_shell_version_is_not_silently_replaced_by_source_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = Mock(return_value="0.6.1")
    monkeypatch.setattr(desktop_backups, "version", installed)
    with pytest.raises(ValueError, match="version"):
        desktop_backups.application_version({"SCAN64_APP_VERSION": ""})
    installed.assert_not_called()
