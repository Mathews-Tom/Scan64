"""Pre-migration SQLite snapshots for desktop application version advances.

``desktop-app-upgrade-pending`` records two lines: the successful baseline
version and its snapshot filename in ``backups``. Until a newer startup commits
``desktop-app-version``, every upgrade attempt reuses that pristine snapshot.
Snapshot filenames and the three-backup retention limit remain unchanged.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import time
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

VERSION_MARKER = "desktop-app-version"
PENDING_UPGRADE_MARKER = "desktop-app-upgrade-pending"
BACKUP_PATTERN = "scan64-*-before-*.db"
BACKUPS_TO_KEEP = 3
_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
type VersionKey = tuple[int, int, int, bool, tuple[tuple[int, int, str], ...]]


def _version_key(value: str) -> VersionKey:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("Desktop application version must be a semantic version")
    prerelease = match.group(4)
    identifiers: list[tuple[int, int, str]] = []
    if prerelease is not None:
        for identifier in prerelease.split("."):
            if identifier.isascii() and identifier.isdigit():
                if len(identifier) > 1 and identifier.startswith("0"):
                    raise ValueError(
                        "Semantic version numeric identifiers must not have leading zeros"
                    )
                identifiers.append((0, int(identifier), ""))
            else:
                identifiers.append((1, 0, identifier))
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        prerelease is None,
        tuple(identifiers),
    )


def application_version(environment: Mapping[str, str]) -> str:
    """Prefer the shell's version, including when reusing an older server bundle."""
    value = environment.get("SCAN64_APP_VERSION")
    if value is None:
        # Source launches do not have a shell. Packaged shells always supply this.
        value = version("scan64")
    _version_key(value)
    return value


def _sync_directory(directory: Path) -> None:
    # POSIX also needs the directory entry flushed for crash-safe publication.
    # Windows cannot open directories with os.open; files are fsynced there too.
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _backup_database(database: Path, backup_dir: Path, app_version: str) -> Path:
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    prefix = f"scan64-{time.time_ns():020d}-before-{app_version}-"
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".partial", dir=backup_dir)
    partial = Path(name)
    destination = partial.with_suffix(".db")
    try:
        os.close(descriptor)
        # Read-only URI prevents accidentally creating a missing source database;
        # SQLite's backup API includes committed WAL pages, unlike a file copy.
        with (
            closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(partial)) as target,
        ):
            source.backup(target)
        with partial.open("rb+") as snapshot:
            os.fsync(snapshot.fileno())
        partial.replace(destination)
        _sync_directory(backup_dir)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _write_marker(data_dir: Path, name: str, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}-", dir=data_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as marker:
            marker.write(value)
            marker.flush()
            os.fsync(marker.fileno())
        temporary.replace(data_dir / name)
        _sync_directory(data_dir)
    finally:
        temporary.unlink(missing_ok=True)


def _pending_snapshot(data_dir: Path, previous: str | None) -> Path | None:
    pending = data_dir / PENDING_UPGRADE_MARKER
    try:
        fields = pending.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    if len(fields) != 2:
        raise ValueError("Invalid desktop pending upgrade marker")
    baseline, name = fields
    baseline_key = _version_key(baseline)
    if previous is not None and _version_key(previous) > baseline_key:
        # The successful version was committed before a crash interrupted cleanup.
        pending.unlink()
        return None
    if previous is None or _version_key(previous) != baseline_key:
        raise ValueError("Desktop pending upgrade baseline does not match version marker")
    if "/" in name or "\\" in name or not re.fullmatch(r"scan64-[0-9]+-before-.+\.db", name):
        raise ValueError("Invalid desktop pending upgrade snapshot name")
    snapshot = data_dir / "backups" / name
    if not snapshot.is_file():
        # Migrations may already have changed the source; never replace this
        # missing pristine snapshot with a backup of a partially migrated DB.
        raise FileNotFoundError(f"Desktop pending upgrade snapshot is missing: {snapshot}")
    return snapshot


def _retain_backups(backup_dir: Path, pending_snapshot: Path) -> None:
    # Reserve one slot for the pending snapshot regardless of wall-clock order.
    others = sorted(
        (path for path in backup_dir.glob(BACKUP_PATTERN) if path != pending_snapshot),
        reverse=True,
    )
    for expired in others[BACKUPS_TO_KEEP - 1 :]:
        expired.unlink()


@dataclass(frozen=True)
class DesktopBackup:
    data_dir: Path
    app_version: str
    previous_version: str | None

    def record_successful_startup(self) -> None:
        """Commit only after migrations and application startup have succeeded."""
        if self.previous_version is not None and _version_key(self.app_version) <= _version_key(
            self.previous_version
        ):
            # Do not lower the high-water mark during a downgrade.
            return
        _write_marker(self.data_dir, VERSION_MARKER, f"{self.app_version}\n")
        # Commit success first. A crash before this unlink leaves a stale pending
        # marker that the next prepare can safely recognize by its old baseline.
        (self.data_dir / PENDING_UPGRADE_MARKER).unlink(missing_ok=True)


def prepare_desktop_backup(data_dir: Path, app_version: str) -> DesktopBackup:
    """Fail closed before any API import; first launch has no previous baseline."""
    current = _version_key(app_version)
    marker = data_dir / VERSION_MARKER
    try:
        previous = marker.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        previous = None
    pending_snapshot = _pending_snapshot(data_dir, previous)
    if previous is not None and current > _version_key(previous):
        database = data_dir / "scan64.db"
        if pending_snapshot is None and database.exists():
            pending_snapshot = _backup_database(database, data_dir / "backups", app_version)
            # Publish the snapshot identity before allowing migrations. Retries
            # share this baseline even if they target another newer version.
            _write_marker(
                data_dir,
                PENDING_UPGRADE_MARKER,
                f"{previous}\n{pending_snapshot.name}\n",
            )
        if pending_snapshot is not None:
            _retain_backups(data_dir / "backups", pending_snapshot)
    return DesktopBackup(data_dir, app_version, previous)
