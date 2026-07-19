from __future__ import annotations

import subprocess
from pathlib import Path

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"


def test_reference_renderer_conforms() -> None:
    result = _run_renderer("reference_renderer_stub")

    assert result.returncode == 0
    assert result.stdout == "Conformance passed: 3 fixtures, 15 visualization commands.\n"
    assert result.stderr == ""


def test_broken_renderer_is_nonconformant() -> None:
    result = _run_renderer("broken_renderer_stub")

    assert result.returncode == 2
    assert "renderer does not support `draw_defence_map`" in result.stderr


def _run_renderer(renderer_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["scan64-conformance", "run", "--renderer", str(FIXTURES_DIR / renderer_name)],
        check=False,
        capture_output=True,
        text=True,
    )
