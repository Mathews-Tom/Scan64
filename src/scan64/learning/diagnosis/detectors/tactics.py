from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence


class KnightForkDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "missed_tactic":
                payload = ev.payload
                if payload.get("tactic_type") == "knight_fork" or payload.get("is_knight_fork"):
                    eval_drop = opportunity.engine_eval_before - opportunity.engine_eval_after
                    if eval_drop >= 2.0:
                        candidates.append(
                            DiagnosisCandidate(
                                skill_id="tactics.fork.knight",
                                confidence=1.0 if payload.get("results_in_material_gain") else 0.8,
                                evidence_ids=[ev.evidence_id],
                            )
                        )
        return candidates


class PinDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "missed_tactic":
                payload = ev.payload
                if payload.get("tactic_type") == "pin" or payload.get("is_pin"):
                    eval_drop = opportunity.engine_eval_before - opportunity.engine_eval_after
                    if eval_drop >= 2.0:
                        candidates.append(
                            DiagnosisCandidate(
                                skill_id="tactics.pin",
                                confidence=1.0 if payload.get("wins_material") else 0.8,
                                evidence_ids=[ev.evidence_id],
                            )
                        )
        return candidates


class OverloadedDefenderDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        candidates = []
        for ev in evidence:
            if ev.kind == "missed_tactic":
                payload = ev.payload
                if payload.get("tactic_type") == "overloaded_defender" or payload.get(
                    "is_overloaded_defender"
                ):
                    eval_drop = opportunity.engine_eval_before - opportunity.engine_eval_after
                    if eval_drop >= 2.0:
                        candidates.append(
                            DiagnosisCandidate(
                                skill_id="tactics.overloaded_defender",
                                confidence=1.0 if payload.get("wins_material") else 0.8,
                                evidence_ids=[ev.evidence_id],
                            )
                        )
        return candidates
