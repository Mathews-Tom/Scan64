import os
import subprocess
import sys
from pathlib import Path

import pytest

from scan64.persistence.database import DEFAULT_DATABASE_URL, database_url_from_environment


def test_database_url_default_preserves_relative_database_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCAN64_DATABASE_URL", raising=False)

    assert database_url_from_environment() == DEFAULT_DATABASE_URL
    assert DEFAULT_DATABASE_URL == "sqlite:///database.db"


def test_database_url_uses_configured_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "scan64.db"
    expected_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("SCAN64_DATABASE_URL", expected_url)

    assert database_url_from_environment() == expected_url

def test_database_url_relocates_the_database(tmp_path: Path) -> None:
    database_path = tmp_path / "configured.db"
    database_url = f"sqlite:///{database_path}"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scan64.persistence.database import create_db_and_tables; create_db_and_tables()",
        ],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=os.environ | {"SCAN64_DATABASE_URL": database_url},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert database_path.is_file()
    assert not (tmp_path / "database.db").exists()


def test_database_url_rejects_blank_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN64_DATABASE_URL", " ")

    with pytest.raises(ValueError, match="SCAN64_DATABASE_URL must not be blank"):
        database_url_from_environment()
