from __future__ import annotations

from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.models import DiagnosisCandidate
from scan64.learning.evidence.models import Evidence


def _evidence(evidence_id: str, **payload: object) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        kind="test",
        position_id="position-1",
        engine_analysis_id="analysis-1",
        claim="test evidence",
        payload={
            "focused_analysis_id": "focused-1",
            "focused_multipv": [{"pv": ["e2e4"]}],
            **payload,
        },
    )


def test_arbitration_retains_one_primary_and_sorted_secondaries() -> None:
    evidence = [
        _evidence("e-hanging", swing_cp=200),
        _evidence("e-pin", swing_cp=200),
        _evidence("e-check", swing_cp=200, missed_type="check"),
    ]
    candidates = [
        DiagnosisCandidate(
            skill_id="tactics.pin", confidence=0.9, evidence_ids=["e-pin"]
        ),
        DiagnosisCandidate(
            skill_id="board_awareness.hanging_piece",
            confidence=0.9,
            evidence_ids=["e-hanging"],
        ),
        DiagnosisCandidate(
            skill_id="threat_processing.missed_check",
            confidence=1.0,
            evidence_ids=["e-check"],
        ),
    ]

    result = arbitrate_diagnoses(candidates, evidence)

    assert result is not None
    primary, secondary = result
    assert primary.skill_id == "board_awareness.hanging_piece"
    assert [candidate.skill_id for candidate in secondary] == ["tactics.pin"]


def test_arbitration_rejects_candidates_without_focused_provenance() -> None:
    evidence = [
        Evidence(
            evidence_id="e-unproven",
            kind="test",
            position_id="position-1",
            engine_analysis_id="analysis-1",
            claim="test evidence",
            payload={"swing_cp": 200},
        )
    ]
    candidates = [
        DiagnosisCandidate(
            skill_id="board_awareness.hanging_piece",
            confidence=1.0,
            evidence_ids=["e-unproven"],
        )
    ]

    assert arbitrate_diagnoses(candidates, evidence) is None
