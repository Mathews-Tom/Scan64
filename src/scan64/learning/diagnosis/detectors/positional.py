from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence


class KingSafetyNeglectDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "positional_analysis":
                payload = ev.payload
                if payload.get("issue") == "king_safety_neglect" or payload.get(
                    "is_king_safety_neglect"
                ):
                    eval_drop = opportunity.engine_eval_before - opportunity.engine_eval_after
                    if eval_drop >= 1.0 and payload.get("incoming_threat"):
                        candidates.append(
                            DiagnosisCandidate(
                                skill_id="positional.king_safety_neglect",
                                confidence=0.85,
                                evidence_ids=[ev.evidence_id],
                            )
                        )
        return candidates
