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
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2").record_successful_startup()
    previous_snapshots = snapshots(tmp_path)
    connect = desktop_backups.sqlite3.connect
    failure = Mock(side_effect=sqlite3.OperationalError("disk full"))
    monkeypatch.setattr(desktop_backups.sqlite3, "connect", failure)
    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        desktop_backups.prepare_desktop_backup(tmp_path, "0.6.3")
    assert marker.read_text() == "0.6.2\n"
    assert snapshots(tmp_path) == previous_snapshots
    assert not list((tmp_path / "backups").glob("*.partial"))
    assert database.is_file()
    monkeypatch.setattr(desktop_backups.sqlite3, "connect", connect)
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.3").record_successful_startup()
    assert len(snapshots(tmp_path)) == 2
    assert marker.read_text() == "0.6.3\n"


def test_four_failed_migrations_reuse_original_snapshot_and_keep_retention_three(
    tmp_path: Path,
) -> None:
    database = create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.0\n", encoding="utf-8")
    for minor in range(1, 4):
        desktop_backups.prepare_desktop_backup(tmp_path, f"0.6.{minor}").record_successful_startup()
    original = None
    for attempt in range(4):
        # Failed migrations can commit partial changes, but cannot call success.
        desktop_backups.prepare_desktop_backup(tmp_path, "0.6.4")
        pending = (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).read_text().splitlines()
        assert pending[0] == "0.6.3"
        assert pending[2] == "0.6.4"
        snapshot = tmp_path / "backups" / pending[1]
        if original is None:
            original = snapshot
        assert snapshot == original
        assert len(snapshots(tmp_path)) == 3
        with closing(sqlite3.connect(snapshot)) as saved:
            assert saved.execute("SELECT name FROM player").fetchall() == [("Saved player",)]
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE player SET name = ?", (f"Failed migration {attempt}",))
            connection.commit()
        assert marker.read_text() == "0.6.3\n"
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.4").record_successful_startup()
    assert original in snapshots(tmp_path)
    assert len(snapshots(tmp_path)) == 3
    assert not (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).exists()
    assert marker.read_text() == "0.6.4\n"


def test_failed_marker_replace_keeps_old_marker_and_reuses_pending_backup(
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
    assert len(snapshots(tmp_path)) == 1
    assert marker.read_text() == "0.6.2\n"
    assert not (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).exists()


def test_clock_running_backwards_keeps_current_pending_snapshot_and_retention_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.0\n", encoding="utf-8")
    clock = Mock(side_effect=[400, 300, 200, 100])
    monkeypatch.setattr(desktop_backups.time, "time_ns", clock)
    for minor in range(1, 4):
        desktop_backups.prepare_desktop_backup(tmp_path, f"0.6.{minor}").record_successful_startup()
    startup = desktop_backups.prepare_desktop_backup(tmp_path, "0.6.4")
    pending = (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).read_text().splitlines()
    snapshot = tmp_path / "backups" / pending[1]
    assert snapshot.name.startswith(f"scan64-{100:020d}-")
    assert snapshot in snapshots(tmp_path)
    assert len(snapshots(tmp_path)) == 3
    for _ in range(4):
        desktop_backups.prepare_desktop_backup(tmp_path, "0.6.4")
        assert snapshot in snapshots(tmp_path)
        assert len(snapshots(tmp_path)) == 3
    assert clock.call_count == 4
    startup.record_successful_startup()
    assert snapshot in snapshots(tmp_path)
    assert len(snapshots(tmp_path)) == 3


def test_new_upgrade_after_success_takes_fresh_baseline(tmp_path: Path) -> None:
    database = create_database(tmp_path)
    (tmp_path / desktop_backups.VERSION_MARKER).write_text("0.6.1\n", encoding="utf-8")
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2").record_successful_startup()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE player SET name = 'Successful migration'")
        connection.commit()
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.3")
    pending = (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).read_text().splitlines()
    assert pending[0] == "0.6.2"
    assert "-before-0.6.3-" in pending[1]
    with closing(sqlite3.connect(tmp_path / "backups" / pending[1])) as saved:
        assert saved.execute("SELECT name FROM player").fetchall() == [("Successful migration",)]
    assert len(snapshots(tmp_path)) == 2


def test_different_upgrade_target_takes_fresh_snapshot(tmp_path: Path) -> None:
    database = create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    original = snapshots(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE player SET name = 'Partial migration'")
        connection.commit()
    startup = desktop_backups.prepare_desktop_backup(tmp_path, "0.6.3")
    pending = (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).read_text().splitlines()
    assert pending[2] == "0.6.3"
    snapshot = tmp_path / "backups" / pending[1]
    assert snapshot not in original
    assert all(path in snapshots(tmp_path) for path in original)
    with closing(sqlite3.connect(snapshot)) as saved:
        assert saved.execute("SELECT name FROM player").fetchall() == [("Partial migration",)]
    startup.record_successful_startup()
    assert marker.read_text() == "0.6.3\n"
    assert not (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).exists()


@pytest.mark.parametrize("remove_directory", [False, True])
def test_missing_pending_snapshot_takes_fresh_backup(
    tmp_path: Path, remove_directory: bool
) -> None:
    database = create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    [snapshot] = snapshots(tmp_path)
    snapshot.unlink()
    if remove_directory:
        snapshot.parent.rmdir()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("INSERT INTO player VALUES (2, 'New data')")
        connection.commit()
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    [fresh] = snapshots(tmp_path)
    assert fresh != snapshot
    with closing(sqlite3.connect(fresh)) as saved:
        assert saved.execute("SELECT name FROM player ORDER BY id").fetchall() == [
            ("Saved player",),
            ("New data",),
        ]
    assert marker.read_text() == "0.6.1\n"
    pending = (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).read_text().splitlines()
    assert pending == ["0.6.1", fresh.name, "0.6.2"]


def test_failed_pending_marker_publish_cannot_allow_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    replace = Path.replace

    def fail_pending_replace(source: Path, destination: Path) -> Path:
        if destination.name == desktop_backups.PENDING_UPGRADE_MARKER:
            raise OSError("pending replace failed")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_pending_replace)
    with pytest.raises(OSError, match="pending replace failed"):
        desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    assert marker.read_text() == "0.6.1\n"
    assert not (tmp_path / desktop_backups.PENDING_UPGRADE_MARKER).exists()
    assert not list(tmp_path.glob(".desktop-app-upgrade-pending-*"))
    monkeypatch.setattr(Path, "replace", replace)
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2").record_successful_startup()
    assert marker.read_text() == "0.6.2\n"


def test_crash_after_success_marker_commit_does_not_reuse_stale_pending_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    startup = desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    pending = tmp_path / desktop_backups.PENDING_UPGRADE_MARKER
    unlink = Path.unlink

    def fail_pending_cleanup(path: Path, missing_ok: bool = False) -> None:
        if path == pending:
            raise OSError("pending cleanup interrupted")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_pending_cleanup)
    with pytest.raises(OSError, match="pending cleanup interrupted"):
        startup.record_successful_startup()
    assert marker.read_text() == "0.6.2\n"
    assert pending.exists()
    monkeypatch.setattr(Path, "unlink", unlink)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE player SET name = 'Successfully upgraded'")
        connection.commit()
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.3")
    baseline, snapshot, target = pending.read_text().splitlines()
    assert baseline == "0.6.2"
    assert target == "0.6.3"
    with closing(sqlite3.connect(tmp_path / "backups" / snapshot)) as saved:
        assert saved.execute("SELECT name FROM player").fetchall() == [("Successfully upgraded",)]
    assert len(snapshots(tmp_path)) == 2


@pytest.mark.parametrize("version", ["0.6.1", "0.6.0"])
@pytest.mark.parametrize("remove_directory", [False, True])
def test_same_or_older_success_clears_pending_and_next_upgrade_saves_new_data(
    tmp_path: Path, version: str, remove_directory: bool
) -> None:
    database = create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    pending = tmp_path / desktop_backups.PENDING_UPGRADE_MARKER
    [original] = snapshots(tmp_path)
    if remove_directory:
        original.unlink()
        original.parent.rmdir()
    desktop_backups.prepare_desktop_backup(tmp_path, version).record_successful_startup()
    assert marker.read_text() == "0.6.1\n"
    assert not pending.exists()
    if not remove_directory:
        assert snapshots(tmp_path) == [original]
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("INSERT INTO player VALUES (2, 'After successful rollback')")
        connection.commit()
    startup = desktop_backups.prepare_desktop_backup(tmp_path, "0.6.3")
    baseline, name, target = pending.read_text().splitlines()
    assert (baseline, target) == ("0.6.1", "0.6.3")
    fresh = tmp_path / "backups" / name
    assert fresh != original
    with closing(sqlite3.connect(fresh)) as saved:
        assert saved.execute("SELECT name FROM player ORDER BY id").fetchall() == [
            ("Saved player",),
            ("After successful rollback",),
        ]
    startup.record_successful_startup()
    assert not pending.exists()


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "0.6.1\n",
        "0.6.1\nsnapshot.db\nunexpected\n",
        "broken\nscan64-100-before-0.6.2-test.db\n0.6.2\n",
        "0.6.1\nscan64-100-before-0.6.2-test.db\nbroken\n",
        "0.6.1\n../scan64-100-before-0.6.2-test.db\n0.6.2\n",
        "0.6.1\n..\\scan64-100-before-0.6.2-test.db\n0.6.2\n",
        "0.6.1\nmanual-export.db\n0.6.2\n",
    ],
)
def test_invalid_pending_marker_fails_closed(tmp_path: Path, contents: str) -> None:
    create_database(tmp_path)
    marker = tmp_path / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    pending = tmp_path / desktop_backups.PENDING_UPGRADE_MARKER
    pending.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        desktop_backups.prepare_desktop_backup(tmp_path, "0.6.2")
    assert snapshots(tmp_path) == []
    assert marker.read_text() == "0.6.1\n"
    assert pending.read_text() == contents


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
