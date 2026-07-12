from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence


class DelayedDevelopmentDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "opening_analysis":
                payload = ev.payload
                if payload.get("issue") == "delayed_development" or payload.get(
                    "is_delayed_development"
                ):
                    if payload.get("tempo_loss", 0) > 1.5:
                        candidates.append(
                            DiagnosisCandidate(
                                skill_id="opening.delayed_development",
                                confidence=0.9,
                                evidence_ids=[ev.evidence_id],
                            )
                        )
        return candidates
