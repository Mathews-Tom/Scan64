from __future__ import annotations

from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.evidence.support import validated_motif_line


class _MotifDetector:
    tactic: str

    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        code = f"tactics.{self.tactic}"
        # Producers convert each integer-cp evaluation independently to pawns.
        before_cp = round(opportunity.engine_eval_before * 100)
        after_cp = round(opportunity.engine_eval_after * 100)
        if before_cp - after_cp < 200:
            return []
        candidates = []
        for item in evidence:
            payload = item.payload
            if (
                item.kind != "missed_tactic"
                or payload.get("tactic_type") != self.tactic
                or validated_motif_line(code, payload, opportunity.played_move) is None
            ):
                continue
            candidates.append(
                DiagnosisCandidate(skill_id=code, confidence=1.0, evidence_ids=[item.evidence_id])
            )
        return candidates


class SkewerDetector(_MotifDetector):
    tactic = "skewer"


class DiscoveredAttackDetector(_MotifDetector):
    tactic = "discovered_attack"


class BackRankDetector(_MotifDetector):
    tactic = "back_rank"
