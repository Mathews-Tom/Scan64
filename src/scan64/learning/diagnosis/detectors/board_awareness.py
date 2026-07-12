from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence


class HangingPieceDetector:
    """Detects when a player blunders a hanging piece."""

    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "blunder_analysis":
                payload = ev.payload
                # Check if it was an undefended piece capture
                if payload.get("blunder_type") == "hanging_piece_lost" or payload.get(
                    "is_hanging_piece_blunder"
                ):
                    eval_drop = opportunity.engine_eval_before - opportunity.engine_eval_after
                    if eval_drop >= 2.0:
                        candidates.append(
                            DiagnosisCandidate(
                                skill_id="board_awareness.hanging_piece",
                                confidence=1.0,
                                evidence_ids=[ev.evidence_id],
                            )
                        )
        return candidates
