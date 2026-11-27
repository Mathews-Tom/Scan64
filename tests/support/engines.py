"""Launch ``tests/fixtures/engines/fake_xboard.py`` as an engine binary."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FAKE_XBOARD = Path(__file__).resolve().parents[1] / "fixtures" / "engines" / "fake_xboard.py"


def fake_xboard_launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Return ``(launcher, command_log)``; the launcher is a runnable engine path."""
    log = tmp_path / "xboard.log"
    monkeypatch.setenv("FAKE_XBOARD_LOG", str(log))
    if os.name == "nt":
        launcher = tmp_path / "fake_xboard.cmd"
        launcher.write_text(f'@"{sys.executable}" "{FAKE_XBOARD}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "fake_xboard"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_XBOARD}" "$@"\n', encoding="utf-8"
        )
        launcher.chmod(0o755)
    return launcher, log
