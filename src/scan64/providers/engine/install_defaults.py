"""The engines an installation uses when no engine is configured (#396).

A packaged Scan64 (the Docker image, the desktop app) names the folder of its
bundled engine profiles in ``SCAN64_BUNDLED_ENGINES_DIR``. On the first start
with that folder and no engine configured, Scan64 records the installation's
engines next to its database and keeps them on every later start:

- no database yet (a fresh install): the bundled engines, Caissa for analysis
  and Arasan for bot moves;
- a database already there (an install from before the bundled engines):
  Stockfish, as before, so its games, bot sessions and analysis keep their
  engine.

``SCAN64_ENGINE_CONFIG``, ``SCAN64_LADDER_ENGINE_CONFIG`` or (outside the
desktop app, whose shell always passes its bundled Stockfish)
``SCAN64_STOCKFISH_PATH`` set explicitly always win, and nothing is recorded
for that start.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import MutableMapping
from pathlib import Path
from typing import Literal, get_args

from sqlalchemy.engine import make_url

from scan64.providers.engine.config import (
    ENGINE_CONFIG_ENV,
    LADDER_ENGINE_CONFIG_ENV,
    STOCKFISH_PATH_ENV,
    EngineConfigurationError,
)

BUNDLED_ENGINES_DIR_ENV = "SCAN64_BUNDLED_ENGINES_DIR"
ANALYSIS_PROFILE = "caissa.toml"
LADDER_PROFILE = "arasan.toml"
RECORD_SUFFIX = ".engines.toml"

type InstallEngines = Literal["bundled", "stockfish"]
INSTALL_ENGINES: tuple[InstallEngines, ...] = get_args(InstallEngines.__value__)

_RECORD_TEXT = """\
# The engines this Scan64 installation uses when no engine is configured (#396).
# Written on its first start: "bundled" (Caissa for analysis, Arasan for bot
# moves) for a fresh install, "stockfish" for an install that had a database
# before the bundled engines. Edit it to switch; SCAN64_ENGINE_CONFIG and
# SCAN64_LADDER_ENGINE_CONFIG always win. See docs/engines.md.
engines = "{engines}"
"""


def database_file(database_url: str) -> Path:
    """The SQLite file of ``database_url``; an in-memory database has none."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise EngineConfigurationError(
            f"{BUNDLED_ENGINES_DIR_ENV} records the installation's engines next to a "
            f"SQLite database file; {url.get_backend_name()} has none"
        )
    database = url.database
    if database is None or database in ("", ":memory:"):
        raise EngineConfigurationError(
            f"{BUNDLED_ENGINES_DIR_ENV} needs a database file to record the "
            f"installation's engines next to; {database_url} has none"
        )
    return Path(database).absolute()


def engines_record(database: Path) -> Path:
    return database.with_name(database.name + RECORD_SUFFIX)


def _read_record(record: Path) -> InstallEngines:
    try:
        with record.open("rb") as stream:
            raw = tomllib.load(stream)
    except tomllib.TOMLDecodeError as error:
        raise EngineConfigurationError(f"Invalid engines record {record}: {error}") from error
    engines = raw.get("engines")
    if set(raw) != {"engines"} or engines not in INSTALL_ENGINES:
        raise EngineConfigurationError(
            f"{record} must hold only engines = one of {', '.join(INSTALL_ENGINES)}"
        )
    return engines  # type: ignore[no-any-return]


def _write_record(record: Path, engines: InstallEngines) -> None:
    record.parent.mkdir(parents=True, exist_ok=True)
    partial = record.with_name(record.name + ".partial")
    partial.write_text(_RECORD_TEXT.format(engines=engines), encoding="utf-8")
    os.replace(partial, record)


def apply_install_engines(
    database_url: str,
    environ: MutableMapping[str, str] = os.environ,
    *,
    honour_stockfish_path: bool = True,
) -> InstallEngines | None:
    """Select this installation's engines in ``environ``; None when not decided here.

    None means an engine is configured explicitly or no bundled engines exist
    (a source checkout): the engine settings in ``environ`` stand as they are.
    """
    configured = [ENGINE_CONFIG_ENV, LADDER_ENGINE_CONFIG_ENV]
    if honour_stockfish_path:
        configured.append(STOCKFISH_PATH_ENV)
    if any(environ.get(name) for name in configured):
        return None
    raw_directory = environ.get(BUNDLED_ENGINES_DIR_ENV)
    if not raw_directory:
        return None
    directory = Path(raw_directory)
    if not directory.is_absolute():
        raise EngineConfigurationError(
            f"{BUNDLED_ENGINES_DIR_ENV} must be an absolute path: {directory}"
        )
    profiles = (directory / ANALYSIS_PROFILE, directory / LADDER_PROFILE)
    for profile in profiles:
        if not profile.is_file():
            raise EngineConfigurationError(
                f"{BUNDLED_ENGINES_DIR_ENV}={directory} has no bundled profile {profile.name}"
            )
    database = database_file(database_url)
    record = engines_record(database)
    if record.is_file():
        engines = _read_record(record)
    else:
        engines = "stockfish" if database.exists() else "bundled"
        _write_record(record, engines)
    if engines == "bundled":
        environ[ENGINE_CONFIG_ENV] = str(profiles[0])
        environ[LADDER_ENGINE_CONFIG_ENV] = str(profiles[1])
    return engines
