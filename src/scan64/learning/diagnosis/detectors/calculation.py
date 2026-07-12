from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence


class StoppedCalculationEarlyDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "calculation_error":
                payload = ev.payload
                if payload.get("error_type") == "stopped_early" or payload.get("is_stopped_early"):
                    # Check sequence involves 3+ plies and eval swings sharply at the end
                    if payload.get("sequence_plies", 0) >= 3 and payload.get("sharp_eval_swing"):
                        candidates.append(
                            DiagnosisCandidate(
                                skill_id="calculation.stopped_too_early",
                                confidence=0.8,
                                evidence_ids=[ev.evidence_id],
                            )
                        )
        return candidates
