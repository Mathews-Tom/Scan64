"""A packaged install's engines when none is configured (#396)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scan64.providers.engine.config import (
    ENGINE_CONFIG_ENV,
    LADDER_ENGINE_CONFIG_ENV,
    EngineConfigurationError,
)
from scan64.providers.engine.install_defaults import (
    BUNDLED_ENGINES_DIR_ENV,
    apply_install_engines,
    engines_record,
)


def _bundled(tmp_path: Path) -> Path:
    directory = tmp_path / "engines"
    directory.mkdir()
    for name in ("caissa.toml", "arasan.toml"):
        (directory / name).write_text("[engine]\nname = 'x'\nbinary_path = 'x'\n", encoding="utf-8")
    return directory


def _url(database: Path) -> str:
    return f"sqlite:///{database.as_posix()}"


def test_a_fresh_install_records_and_selects_the_bundled_engines(tmp_path: Path) -> None:
    bundled = _bundled(tmp_path)
    database = tmp_path / "data" / "scan64.db"
    environ = {BUNDLED_ENGINES_DIR_ENV: str(bundled)}
    assert apply_install_engines(_url(database), environ) == "bundled"
    assert environ[ENGINE_CONFIG_ENV] == str(bundled / "caissa.toml")
    assert environ[LADDER_ENGINE_CONFIG_ENV] == str(bundled / "arasan.toml")
    assert 'engines = "bundled"' in engines_record(database).read_text(encoding="utf-8")
    # The database exists from now on; the recorded choice is what decides.
    database.write_bytes(b"x")
    later = {BUNDLED_ENGINES_DIR_ENV: str(bundled)}
    assert apply_install_engines(_url(database), later) == "bundled"
    assert later[ENGINE_CONFIG_ENV] == str(bundled / "caissa.toml")


def test_an_install_with_a_database_keeps_stockfish(tmp_path: Path) -> None:
    bundled = _bundled(tmp_path)
    database = tmp_path / "scan64.db"
    database.write_bytes(b"x")
    environ = {BUNDLED_ENGINES_DIR_ENV: str(bundled)}
    assert apply_install_engines(_url(database), environ) == "stockfish"
    assert ENGINE_CONFIG_ENV not in environ
    assert LADDER_ENGINE_CONFIG_ENV not in environ
    assert 'engines = "stockfish"' in engines_record(database).read_text(encoding="utf-8")


def test_an_edited_record_switches_the_engines(tmp_path: Path) -> None:
    bundled = _bundled(tmp_path)
    database = tmp_path / "scan64.db"
    database.write_bytes(b"x")
    engines_record(database).write_text('engines = "bundled"\n', encoding="utf-8")
    environ = {BUNDLED_ENGINES_DIR_ENV: str(bundled)}
    assert apply_install_engines(_url(database), environ) == "bundled"
    assert environ[ENGINE_CONFIG_ENV] == str(bundled / "caissa.toml")


@pytest.mark.parametrize("variable", [ENGINE_CONFIG_ENV, LADDER_ENGINE_CONFIG_ENV])
def test_an_explicit_engine_wins_and_nothing_is_recorded(tmp_path: Path, variable: str) -> None:
    database = tmp_path / "scan64.db"
    environ = {BUNDLED_ENGINES_DIR_ENV: str(_bundled(tmp_path)), variable: "/opt/engine.toml"}
    assert apply_install_engines(_url(database), environ) is None
    assert environ == {
        BUNDLED_ENGINES_DIR_ENV: str(tmp_path / "engines"),
        variable: "/opt/engine.toml",
    }
    assert not engines_record(database).exists()


def test_without_bundled_engines_nothing_is_decided(tmp_path: Path) -> None:
    database = tmp_path / "scan64.db"
    environ: dict[str, str] = {}
    assert apply_install_engines(_url(database), environ) is None
    assert environ == {}
    assert not engines_record(database).exists()


def test_invalid_settings_fail_fast(tmp_path: Path) -> None:
    bundled = _bundled(tmp_path)
    database = tmp_path / "scan64.db"
    with pytest.raises(EngineConfigurationError, match="absolute"):
        apply_install_engines(_url(database), {BUNDLED_ENGINES_DIR_ENV: "engines"})
    analysis_only = tmp_path / "analysis-only"
    analysis_only.mkdir()
    (analysis_only / "caissa.toml").write_text("", encoding="utf-8")
    with pytest.raises(EngineConfigurationError, match="arasan.toml"):
        apply_install_engines(_url(database), {BUNDLED_ENGINES_DIR_ENV: str(analysis_only)})
    with pytest.raises(EngineConfigurationError, match="database file"):
        apply_install_engines("sqlite://", {BUNDLED_ENGINES_DIR_ENV: str(bundled)})
    engines_record(database).write_text('engines = "lc0"\n', encoding="utf-8")
    with pytest.raises(EngineConfigurationError, match="bundled, stockfish"):
        apply_install_engines(_url(database), {BUNDLED_ENGINES_DIR_ENV: str(bundled)})
    assert not database.exists()
