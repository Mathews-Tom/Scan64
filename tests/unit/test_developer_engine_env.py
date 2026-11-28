"""No test reads the developer's engine configuration from the environment."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scan64.providers.engine import (
    ENGINE_CONFIG_ENV,
    STOCKFISH_PATH_ENV,
    EngineConfig,
    active_engine_profile,
)
from scan64.providers.engine.config import LADDER_ENGINE_CONFIG_ENV, ladder_engine_profile

ROOT = Path(__file__).parents[2]


def test_every_test_starts_from_the_default_engine_profile() -> None:
    # ``get``, not ``in os.environ``: a failure must not print the environment.
    assert os.environ.get(ENGINE_CONFIG_ENV) is None
    assert os.environ.get(STOCKFISH_PATH_ENV) is None
    assert os.environ.get(LADDER_ENGINE_CONFIG_ENV) is None
    assert ladder_engine_profile() is None
    profile = active_engine_profile()
    assert (profile.name, profile.binary_path, profile.source) == ("stockfish", "stockfish", None)
    assert EngineConfig().binary_path == "stockfish"


def test_a_test_sets_the_engine_variable_it_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STOCKFISH_PATH_ENV, str(tmp_path / "stockfish"))
    assert active_engine_profile().binary_path == str(tmp_path / "stockfish")


def test_the_developer_engine_variables_do_not_reach_a_test(tmp_path: Path) -> None:
    # A developer shell that selects a profile and a Stockfish binary. The
    # profile does not exist, so any test that read it would fail.
    environment = os.environ | {
        ENGINE_CONFIG_ENV: str(tmp_path / "developer-engine.toml"),
        LADDER_ENGINE_CONFIG_ENV: str(tmp_path / "developer-ladder.toml"),
        STOCKFISH_PATH_ENV: str(tmp_path / "developer-stockfish"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"{Path(__file__).relative_to(ROOT).as_posix()}"
            "::test_every_test_starts_from_the_default_engine_profile",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
