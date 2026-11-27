"""CI may skip a job only when no changed file can affect it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci_areas.py"
spec = importlib.util.spec_from_file_location("ci_areas", SCRIPT)
assert spec is not None and spec.loader is not None
ci_areas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_areas)


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        # An unrecognised file runs everything rather than nothing.
        ([".gitignore"], {"python", "web", "desktop"}),
        (["docs/content/new-dataset.csv"], {"python", "web", "desktop"}),
        # Prose that no check reads.
        (["README.md", "docs/engines.md", "assets/logo.svg"], set()),
        # Tests hash the evaluation reports, so they are not prose.
        (["docs/evaluation/real-game-diagnosis.md"], {"python"}),
        (["docs/lesson-packs-example.json"], {"python"}),
        # Fixture READMEs belong to the tests that read them.
        (["tests/fixtures/v0_9_0/README.md"], {"python"}),
        (["apps/scan64-web/src/App.tsx"], {"web"}),
        (["apps/scan64-web/README.md"], {"web"}),
        (["schemas/board-lines-facts.schema.json"], {"python", "web"}),
        (["src/scan64/desktop_server.py"], {"python", "desktop"}),
        (["uv.lock"], {"python", "desktop"}),
        # Changing the gate itself runs the whole gate.
        ([".github/workflows/ci.yml"], {"python", "web", "desktop"}),
        (["scripts/check.sh"], {"python", "web", "desktop"}),
        ([".github/workflows/release.yml", ".github/CODEOWNERS"], set()),
        (["src/scan64/api/play.py", "apps/scan64-web/src/App.tsx"], {"python", "web"}),
    ],
)
def test_changed_paths_select_every_area_they_can_affect(
    paths: list[str], expected: set[str]
) -> None:
    assert ci_areas.select(paths) == expected


def test_output_names_every_area_for_github_outputs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", ["docs/engines.md\n", "src/scan64/api/play.py\n"])
    assert ci_areas.main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "python=true",
        "web=false",
        "desktop=false",
    ]


def test_empty_change_list_fails_instead_of_skipping_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin", [])
    assert ci_areas.main([]) == 1
