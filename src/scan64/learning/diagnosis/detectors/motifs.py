from __future__ import annotations

import chess

from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.evidence.motifs import motif_payloads
from scan64.learning.evidence.support import supports_saved_diagnosis


class _MotifDetector:
    tactic: str

    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        code = f"tactics.{self.tactic}"
        if opportunity.engine_eval_before - opportunity.engine_eval_after < 2.0:
            return []
        candidates = []
        for item in evidence:
            payload = item.payload
            if (
                item.kind != "missed_tactic"
                or payload.get("tactic_type") != self.tactic
                or not supports_saved_diagnosis(code, payload)
            ):
                continue
            fen, line = payload.get("before_fen"), payload.get("fast_line")
            if not isinstance(fen, str) or not isinstance(line, list) or not line:
                continue
            if not all(isinstance(uci, str) for uci in line):
                continue
            try:
                board = chess.Board(fen)
                played = board.parse_san(opportunity.played_move)
            except ValueError:
                continue
            if line[0] == played.uci() or payload.get("best_move") != line[0]:
                continue
            for proof in motif_payloads(board, line, payload.get("source_score_mate")):
                if proof["tactic_type"] == self.tactic and all(
                    payload.get(key) == value for key, value in proof.items()
                ):
                    candidates.append(
                        DiagnosisCandidate(
                            skill_id=code, confidence=1.0, evidence_ids=[item.evidence_id]
                        )
                    )
                    break
        return candidates


class SkewerDetector(_MotifDetector):
    tactic = "skewer"


class DiscoveredAttackDetector(_MotifDetector):
    tactic = "discovered_attack"


class BackRankDetector(_MotifDetector):
    tactic = "back_rank"
