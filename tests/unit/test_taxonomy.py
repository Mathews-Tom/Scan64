import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scan64.learning.diagnosis.taxonomy.models import SkillDefinition, SkillTier
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES


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
        minimum_engine_evidence="Eval change >= 2.0"
    )
    assert skill.skill_id == "tactics.fork.knight"
    assert skill.tier == SkillTier.EVENT

def test_skill_definition_schema_matches_file() -> None:
    schema_path = Path("schemas/taxonomy.schema.json")
    assert schema_path.exists(), "Schema file must be generated"

    saved_schema = json.loads(schema_path.read_text())
    current_schema = SkillDefinition.model_json_schema()

    msg = "Schema file is out of date. Run generate_taxonomy_schema.py"
    assert saved_schema == current_schema, msg

def test_skill_definition_requires_fields() -> None:
    with pytest.raises(ValidationError):
        SkillDefinition(
            skill_id="tactics.fork.knight",
            name="Knight Fork",
            # missing parent_id and others
        )

def test_seed_codes_are_valid() -> None:
    # SEED_CODES instantiation already triggered Pydantic validation,
    # but we can do a sanity check to ensure all 10 are present.
    assert len(SEED_CODES) == 10

    for code_id, code in SEED_CODES.items():
        assert code.skill_id == code_id
        # Check that it ends up in a valid top-level category prefix
        valid_parents = [
            "board_awareness", "tactics", "threat_processing",
            "candidate_move_generation", "calculation", "positional",
            "opening", "endgame", "behaviour_and_metacognition"
        ]

        # It should either be one of the top level, or its parent_id should be in the hierarchy
        # We just check the top-level categories explicitly for the seeds
        is_valid_parent = any(code.parent_id.startswith(p) for p in valid_parents)
        assert is_valid_parent, f"{code_id} has invalid parent {code.parent_id}"
