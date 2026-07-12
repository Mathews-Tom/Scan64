import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scan64.learning.diagnosis.taxonomy.models import SkillDefinition, SkillTier


def test_skill_definition_valid_creation() -> None:
    skill = SkillDefinition(
        skill_id="tactics.fork.knight",
        name="Knight Fork",
        parent_id="tactics.fork",
        tier=SkillTier.EVENT,
        detection_requirements="Knight attacks two undefended or higher-value pieces.",
        positive_examples=["4k3/8/8/3N4/8/8/2q5/4K3 w - - 0 1"],
        counterexamples=["4k3/8/8/3N4/8/8/2q1n3/4K3 w - - 0 1"],
        confidence_calculation="1.0 if both targets are undefended, 0.8 otherwise.",
        compatible_exercise_templates=["find_the_fork", "visualize_knight_move"],
        incompatible_diagnoses=["tactics.fork.bishop"],
        minimum_engine_evidence="Eval change >= 2.0",
    )
    assert skill.skill_id == "tactics.fork.knight"
    assert skill.tier == SkillTier.EVENT


def test_skill_definition_schema_matches_file() -> None:
    schema_path = Path("schemas/taxonomy.schema.json")
    assert schema_path.exists(), "Schema file must be generated"

    saved_schema = json.loads(schema_path.read_text())
    current_schema = SkillDefinition.model_json_schema()

    assert saved_schema == current_schema, (
        "Schema file is out of date. Run generate_taxonomy_schema.py"
    )


def test_skill_definition_requires_fields() -> None:
    with pytest.raises(ValidationError):
        SkillDefinition(
            skill_id="tactics.fork.knight",
            name="Knight Fork",
            # missing parent_id and others
        )
