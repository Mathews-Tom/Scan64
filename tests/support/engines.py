"""Launch the fake engines in ``tests/fixtures/engines`` as engine binaries."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "engines"
FAKE_XBOARD = FIXTURES / "fake_xboard.py"
FAKE_UCI = FIXTURES / "fake_uci.py"
FAKE_VARIANT_UCI = FIXTURES / "fake_variant_uci.py"


def _launcher(script: Path, tmp_path: Path) -> Path:
    if os.name == "nt":
        launcher = tmp_path / f"{script.stem}.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / script.stem
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        launcher.chmod(0o755)
    return launcher


def fake_xboard_launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Return ``(launcher, command_log)``; the launcher is a runnable engine path."""
    log = tmp_path / "xboard.log"
    monkeypatch.setenv("FAKE_XBOARD_LOG", str(log))
    return _launcher(FAKE_XBOARD, tmp_path), log


def fake_uci_launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A UCI engine that declares no MultiPV; returns ``(launcher, command_log)``."""
    log = tmp_path / "uci.log"
    monkeypatch.setenv("FAKE_UCI_LOG", str(log))
    return _launcher(FAKE_UCI, tmp_path), log


def fake_variant_uci_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variants: str, *, illegal: bool = False
) -> Path:
    """A UCI engine declaring ``variants`` (comma-separated; empty: no UCI_Variant)."""
    monkeypatch.setenv("FAKE_VARIANTS", variants)
    monkeypatch.setenv("FAKE_VARIANT_ILLEGAL", "1" if illegal else "0")
    return _launcher(FAKE_VARIANT_UCI, tmp_path)


FAKE_TRACE_UCI = FIXTURES / "fake_trace_uci.py"


def fake_trace_uci_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: Path,
    stderr: Path | None = None,
    *,
    hang: bool = False,
) -> tuple[Path, Path]:
    """A UCI engine replaying the recorded ``eval`` session; returns ``(launcher, log)``."""
    log = tmp_path / "trace.log"
    monkeypatch.setenv("FAKE_TRACE_LOG", str(log))
    monkeypatch.setenv("FAKE_TRACE_STDOUT", str(stdout))
    if stderr is None:
        monkeypatch.delenv("FAKE_TRACE_STDERR", raising=False)
    else:
        monkeypatch.setenv("FAKE_TRACE_STDERR", str(stderr))
    if hang:
        monkeypatch.setenv("FAKE_TRACE_HANG", "1")
    else:
        monkeypatch.delenv("FAKE_TRACE_HANG", raising=False)
    return _launcher(FAKE_TRACE_UCI, tmp_path), log
