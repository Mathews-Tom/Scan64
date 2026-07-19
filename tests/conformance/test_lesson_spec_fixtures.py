import json
from pathlib import Path

import pytest
from chess_lesson_spec import LessonSpec

FIXTURES_DIR = Path(__file__).parent / "lesson_spec"


@pytest.mark.parametrize("fixture_path", list(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.name)
def test_fixture_roundtrip(fixture_path):
    with open(fixture_path) as f:
        data = json.load(f)

    # 1. Deserialize to Pydantic model (validates automatically)
    model = LessonSpec.model_validate(data)

    # 2. Serialize back to dictionary, excluding unset values so we can compare exactly
    dumped = model.model_dump(mode="json", exclude_unset=True)

    # 3. Assert round-trip fidelity
    # The dumped dictionary should match the original data exactly
    assert dumped == data


def test_visualizations_have_description():
    # Load the visualization fixture specifically
    viz_fixture = FIXTURES_DIR / "valid_visualizations.json"
    with open(viz_fixture) as f:
        data = json.load(f)

    model = LessonSpec.model_validate(data)

    assert model.explanation is not None
    # Ensure every visualization command has a description that is non-empty
    for viz in model.explanation.visualizations:
        assert hasattr(viz, "description")
        assert isinstance(viz.description, str)
        assert len(viz.description.strip()) > 0
