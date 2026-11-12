"""Read-time policy for detector-derived game lessons, without deleting history."""

from __future__ import annotations

import chess
from chess_lesson_spec import LessonSpec
from sqlmodel import Session

from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION, hanging_piece_proof
from scan64.learning.evidence.models import Evidence


def visible_game_lesson(
    opportunity: PersistedLessonOpportunity, session: Session
) -> LessonSpec | None:
    diagnosis = opportunity.lesson_spec.get("diagnosis")
    if not isinstance(diagnosis, dict):
        return None
    primary = diagnosis.get("primary")
    if not isinstance(primary, str) or primary not in TRUSTED_CODES:
        return None
    refs = diagnosis.get("evidence_refs")
    if not isinstance(refs, list):
        return None
    proof_evidence: list[Evidence] = []
    for evidence_id in refs:
        if not isinstance(evidence_id, str):
            continue
        evidence = session.get(Evidence, evidence_id)
        if (
            evidence is not None
            and evidence.payload.get("diagnosis_rule_version") == DIAGNOSIS_RULE_VERSION
        ):
            proof_evidence.append(evidence)
    if not proof_evidence:
        return None
    spec = LessonSpec.model_validate(opportunity.lesson_spec)
    if spec.diagnosis is None:
        return None
    if primary == "board_awareness.hanging_piece":
        for evidence in proof_evidence:
            payload = evidence.payload
            line = payload.get("proving_line")
            played = payload.get("played_move")
            if not isinstance(line, list) or not all(isinstance(move, str) for move in line):
                continue
            if not isinstance(played, str):
                continue
            try:
                after = chess.Board(spec.source.fen)
                mover = after.turn
                after.push_san(played)
            except ValueError:
                continue
            proof = hanging_piece_proof(after, mover, line)
            if proof is not None and all(
                payload.get(key) == proof[key] for key in ("hanging_square", "hanging_piece")
            ):
                break
        else:
            return None
    # Parse made a detached copy: old rows and evidence remain unchanged.
    spec.diagnosis.secondary = []
    return spec
