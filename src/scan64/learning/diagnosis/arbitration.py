from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from scan64.learning.diagnosis.models import DiagnosisCandidate
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.evidence.models import Evidence


def _candidate_payloads(
    candidate: DiagnosisCandidate,
    evidence_by_id: dict[str, Evidence],
) -> list[dict[str, Any]]:
    payloads = []
    for evidence_id in candidate.evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise RuntimeError(
                f"Diagnosis candidate {candidate.skill_id!r} references unknown evidence "
                f"{evidence_id!r}"
            )
        payloads.append(evidence.payload)
    return payloads


def _has_focused_provenance(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("focused_analysis_id"), str) and isinstance(
        payload.get("focused_multipv"), list
    )


def _maximum_swing(payloads: list[dict[str, Any]]) -> int:
    swings = [payload.get("swing_cp") for payload in payloads]
    return max((swing for swing in swings if isinstance(swing, int)), default=0)


def _meets_declared_minimum_engine_evidence(
    candidate: DiagnosisCandidate,
    payloads: list[dict[str, Any]],
) -> bool:
    if not payloads or not all(_has_focused_provenance(payload) for payload in payloads):
        return False
    requirement = SEED_CODES[candidate.skill_id].minimum_engine_evidence
    swing_cp = _maximum_swing(payloads)
    if requirement in {"Eval drop >= 2.0", "Eval change >= 2.0"}:
        return swing_cp >= 200
    if requirement == "Missed mate or +3.0 advantage via check.":
        return swing_cp >= 300 and any(
            payload.get("missed_type") == "check" for payload in payloads
        )
    if requirement == "Missed +2.0 advantage via capture.":
        return swing_cp >= 200 and any(
            payload.get("missed_type") == "capture" for payload in payloads
        )
    if requirement == "Blunder causing immediate loss of material.":
        return any(payload.get("opponent_executed_threat") is True for payload in payloads)
    if requirement == "Sequence involves 3+ plies, eval swings sharply at the end.":
        return any(
            isinstance(payload.get("sequence_plies"), int)
            and payload["sequence_plies"] >= 3
            and payload.get("sharp_eval_swing") is True
            for payload in payloads
        )
    if requirement == "Engine detects loss of tempo > 1.5 in opening.":
        return any(
            isinstance(payload.get("tempo_loss"), (int, float))
            and payload["tempo_loss"] > 1.5
            for payload in payloads
        )
    if requirement == "Eval drop due to incoming mate threat or heavy attack.":
        return any(bool(payload.get("incoming_threat")) for payload in payloads)
    raise RuntimeError(f"Unsupported engine-evidence requirement: {requirement!r}")


def arbitrate_diagnoses(
    candidates: Iterable[DiagnosisCandidate],
    evidence: Iterable[Evidence],
) -> tuple[DiagnosisCandidate, tuple[DiagnosisCandidate, ...]] | None:
    """Choose one deterministic primary diagnosis and retain eligible competitors."""
    evidence_by_id = {item.evidence_id: item for item in evidence}
    best_by_skill: dict[str, DiagnosisCandidate] = {}
    for candidate in candidates:
        if candidate.skill_id not in SEED_CODES:
            raise RuntimeError(
                f"Diagnosis candidate has an unknown taxonomy code: {candidate.skill_id!r}"
            )
        payloads = _candidate_payloads(candidate, evidence_by_id)
        if not _meets_declared_minimum_engine_evidence(candidate, payloads):
            continue
        previous = best_by_skill.get(candidate.skill_id)
        if previous is None or (candidate.confidence, tuple(candidate.evidence_ids)) > (
            previous.confidence,
            tuple(previous.evidence_ids),
        ):
            best_by_skill[candidate.skill_id] = candidate
    if not best_by_skill:
        return None
    ranked = sorted(
        best_by_skill.values(),
        key=lambda candidate: (-candidate.confidence, candidate.skill_id),
    )
    return ranked[0], tuple(ranked[1:])
