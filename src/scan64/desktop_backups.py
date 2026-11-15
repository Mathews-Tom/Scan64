"""Pre-migration SQLite snapshots for desktop application version advances."""

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
    finally:
        partial.unlink(missing_ok=True)
    for expired in sorted(backup_dir.glob(BACKUP_PATTERN), reverse=True)[BACKUPS_TO_KEEP:]:
        expired.unlink()
    return destination


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
        descriptor, name = tempfile.mkstemp(prefix=".desktop-app-version-", dir=self.data_dir)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as marker:
                marker.write(f"{self.app_version}\n")
                marker.flush()
                os.fsync(marker.fileno())
            temporary.replace(self.data_dir / VERSION_MARKER)
        finally:
            temporary.unlink(missing_ok=True)


def prepare_desktop_backup(data_dir: Path, app_version: str) -> DesktopBackup:
    """Fail closed before any API import; first launch has no previous baseline."""
    current = _version_key(app_version)
    marker = data_dir / VERSION_MARKER
    try:
        previous = marker.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        previous = None
    if previous is not None and current > _version_key(previous):
        database = data_dir / "scan64.db"
        if database.exists():
            _backup_database(database, data_dir / "backups", app_version)
    return DesktopBackup(data_dir, app_version, previous)
