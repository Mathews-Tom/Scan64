"""The committed documentation site matches its Markdown sources (#382)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
SITE = REPO / "site" / "docs"


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_docs", REPO / "scripts/site/build_docs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_docs"] = module
    spec.loader.exec_module(module)
    return module


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_committed_pages_match_a_fresh_build(tmp_path: Path) -> None:
    _builder().build(tmp_path)
    built, committed = _files(tmp_path), _files(SITE)
    assert sorted(built) == sorted(committed), "Run: uv run python scripts/site/build_docs.py"
    stale = [name for name in built if built[name] != committed[name]]
    assert stale == [], f"Stale pages {stale}. Run: uv run python scripts/site/build_docs.py"


def test_github_anchors_follow_github_rules() -> None:
    slug = _builder().github_slug
    assert slug("Stable from 1.0") == "stable-from-10"
    assert slug("Chess variants (#398)") == "chess-variants-398"
    assert slug("Health meters during play") == "health-meters-during-play"
