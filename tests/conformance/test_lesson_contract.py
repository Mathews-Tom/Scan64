from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from chess_lesson_spec import AcceptedMove, Hint, LessonSpec, MasteryImpact, Verification
from chess_lesson_spec.fixtures import fixture_paths
from pydantic import ValidationError

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson


def fixture(name: str) -> dict[str, object]:
    path = next(path for path in fixture_paths() if path.name == name)
    data: dict[str, object] = json.loads(path.read_text())
    return data


def test_generated_schema_matches_workspace_contract() -> None:
    path = Path(__file__).resolve().parents[2] / "schemas/lesson-spec.schema.json"
    assert json.loads(path.read_text()) == LessonSpec.model_json_schema()


@pytest.mark.parametrize("name", ["valid_external.json", "valid_external_promotion.json"])
def test_external_line_is_source_verified_without_invented_engine_or_mastery(name: str) -> None:
    data = fixture(name)
    lesson = LessonSpec.model_validate(data)
    verify_lesson(lesson)
    assert lesson.schema_version == "0.2.0"
    assert lesson.diagnosis is None
    assert lesson.mastery is None
    assert lesson.interaction.accepted_moves == []
    assert lesson.verification.model_dump(exclude_unset=True) == {"status": "source_verified"}
    assert lesson.model_dump(mode="json", exclude_unset=True) == data


@pytest.mark.parametrize("kind", ["custom", "external"])
def test_only_player_game_requires_diagnosis(kind: str) -> None:
    data = fixture("valid_minimal.json")
    data["source"] = {**data["source"], "kind": kind}
    del data["diagnosis"]
    assert LessonSpec.model_validate(data).diagnosis is None


@pytest.mark.parametrize("diagnosis", [None, "missing"])
def test_player_game_cannot_omit_diagnosis(diagnosis: object) -> None:
    data = fixture("valid_minimal.json")
    if diagnosis == "missing":
        del data["diagnosis"]
    else:
        data["diagnosis"] = diagnosis
    with pytest.raises(ValidationError, match="require a diagnosis"):
        LessonSpec.model_validate(data)


@pytest.mark.parametrize("status", ["verified", "unverified"])
def test_engine_verification_still_requires_engine(status: str) -> None:
    with pytest.raises(ValidationError, match="requires an engine"):
        Verification(status=status)


def test_player_game_cannot_use_source_verification() -> None:
    data = fixture("valid_external.json")
    data["source"] = {**data["source"], "kind": "player_game"}
    data["diagnosis"] = {"primary": "test", "confidence": 1.0}
    with pytest.raises(ValidationError, match="external source"):
        LessonSpec.model_validate(data)


@pytest.mark.parametrize("solution", [[], ["e4"], ["a7a8k"]])
def test_external_contract_requires_nonempty_uci_solution(solution: list[str]) -> None:
    data = fixture("valid_external.json")
    data["interaction"] = {"input": "move", "maximum_attempts": 3, "solution": solution}
    with pytest.raises(ValidationError):
        LessonSpec.model_validate(data)


@pytest.mark.parametrize(
    ("solution", "message"),
    [
        (["e2e5"], "illegal UCI move"),
        (["e2e4", "d8h4"], "end on a solver move"),
    ],
)
def test_external_verifier_checks_full_line_and_solver_parity(
    solution: list[str], message: str
) -> None:
    data = fixture("valid_external.json")
    data["interaction"] = {"input": "move", "maximum_attempts": 3, "solution": solution}
    with pytest.raises(LessonVerificationError, match=message):
        verify_lesson(LessonSpec.model_validate(data))


def test_external_verifier_requires_exact_promotion_piece() -> None:
    data = fixture("valid_external_promotion.json")
    data["interaction"] = {"input": "move", "maximum_attempts": 3, "solution": ["a7a8"]}
    with pytest.raises(LessonVerificationError, match="illegal UCI move"):
        verify_lesson(LessonSpec.model_validate(data))


def test_external_verifier_rejects_invalid_position() -> None:
    data = fixture("valid_external.json")
    data["source"] = {"kind": "external", "fen": "8/8/8/8/8/8/8/8 w - - 0 1"}
    with pytest.raises(LessonVerificationError, match="invalid chess position"):
        verify_lesson(LessonSpec.model_validate(data))


def test_legacy_single_move_fixture_still_verifies_and_round_trips() -> None:
    data = fixture("valid_legacy_0_1_0.json")
    lesson = LessonSpec.model_validate(data)
    verify_lesson(
        lesson, EngineAnalysis(position_id=uuid4(), raw_result=[{"pv": ["e2e4", "e7e5"]}])
    )
    assert lesson.schema_version == "0.1.0"
    assert lesson.interaction.solution == []
    assert lesson.model_dump(mode="json", exclude_unset=True) == data


def test_player_game_cannot_skip_engine_analysis() -> None:
    lesson = LessonSpec.model_validate(fixture("valid_legacy_0_1_0.json"))
    with pytest.raises(LessonVerificationError, match="requires objective analysis"):
        verify_lesson(lesson)


def test_mutated_player_game_cannot_bypass_engine_verification() -> None:
    lesson = LessonSpec.model_validate(fixture("valid_external.json"))
    lesson.source.kind = "player_game"
    with pytest.raises(LessonVerificationError, match="external source"):
        verify_lesson(lesson)


def test_external_source_verification_still_checks_overlays() -> None:
    lesson = LessonSpec.model_validate(fixture("valid_external.json"))
    lesson.hints = [Hint(level=1, kind="prompt", text="Invalid", squares=["z9"])]
    with pytest.raises(LessonVerificationError, match="Invalid square"):
        verify_lesson(lesson)


SOURCE_CLAIMS: list[tuple[str, object]] = [
    ("accepted_moves", [{"san": "Qh5"}]),
    ("engine", "Stockfish 18"),
    ("engine", ""),
    ("engine_binary_digest", "digest"),
    ("engine_binary_digest", ""),
    ("nodes", 1),
    ("nodes", 0),
    ("multipv", 1),
    ("multipv", 0),
    ("mastery", {"skill_key": "tactics.fork", "delta": 0.1}),
]


@pytest.mark.parametrize(("field", "value"), SOURCE_CLAIMS)
def test_source_verification_rejects_unproved_claims_in_contract(
    field: str, value: object
) -> None:
    data = LessonSpec.model_validate(fixture("valid_external.json")).model_dump(mode="json")
    if field == "accepted_moves":
        data["interaction"][field] = value
    elif field == "mastery":
        data[field] = value
    else:
        data["verification"][field] = value
    with pytest.raises(ValidationError, match="Source verification cannot claim"):
        LessonSpec.model_validate(data)


@pytest.mark.parametrize(("field", "value"), SOURCE_CLAIMS)
def test_source_verifier_rechecks_mutated_unproved_claims(field: str, value: object) -> None:
    lesson = LessonSpec.model_validate(fixture("valid_external.json"))
    if field == "accepted_moves":
        lesson.interaction.accepted_moves = [AcceptedMove(san="Qh5")]
    elif field == "mastery":
        lesson.mastery = MasteryImpact(skill_key="tactics.fork", delta=0.1)
    else:
        setattr(lesson.verification, field, value)
    with pytest.raises(LessonVerificationError, match="Source verification cannot claim"):
        verify_lesson(lesson)


def test_source_verification_round_trips_empty_claims() -> None:
    lesson = LessonSpec.model_validate(fixture("valid_external.json"))
    # Default nulls/empty accepted moves also serialize without implying any claim.
    restored = LessonSpec.model_validate_json(lesson.model_dump_json())
    verify_lesson(restored)
    assert restored.interaction.accepted_moves == []
    assert restored.mastery is None
    assert restored.verification.engine is None
