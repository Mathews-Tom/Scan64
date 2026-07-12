from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence


class MissedCheckDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "missed_opportunity":
                payload = ev.payload
                if payload.get("missed_type") == "check" or payload.get("is_missed_check"):
                    # Check confidence
                    confidence = 1.0 if payload.get("was_unique_best") else 0.8
                    candidates.append(
                        DiagnosisCandidate(
                            skill_id="threat_processing.missed_check",
                            confidence=confidence,
                            evidence_ids=[ev.evidence_id],
                        )
                    )
        return candidates


class MissedCaptureDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "missed_opportunity":
                payload = ev.payload
                if payload.get("missed_type") == "capture" or payload.get("is_missed_capture"):
                    candidates.append(
                        DiagnosisCandidate(
                            skill_id="threat_processing.missed_capture",
                            confidence=1.0 if payload.get("was_only_winning_line") else 0.8,
                            evidence_ids=[ev.evidence_id],
                        )
                    )
        return candidates


class MissedDirectThreatDetector:
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
                if payload.get("blunder_type") == "missed_direct_threat" or payload.get(
                    "is_missed_direct_threat"
                ):
                    # Check if opponent immediately executes the threat
                    confidence = 1.0 if payload.get("opponent_executed_threat") else 0.8
                    candidates.append(
                        DiagnosisCandidate(
                            skill_id="threat_processing.missed_direct_threat",
                            confidence=confidence,
                            evidence_ids=[ev.evidence_id],
                        )
                    )
        return candidates
