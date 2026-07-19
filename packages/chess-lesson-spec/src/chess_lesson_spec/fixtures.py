from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def fixture_paths() -> tuple[Path, ...]:
    """Return every published LessonSpec conformance fixture."""
    fixtures_directory = Path(str(files("chess_lesson_spec").joinpath("fixtures")))
    fixtures = tuple(sorted(fixtures_directory.glob("*.json")))
    if not fixtures:
        raise RuntimeError("The chess-lesson-spec package contains no conformance fixtures.")
    return fixtures
