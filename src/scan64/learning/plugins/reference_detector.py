from __future__ import annotations

from dataclasses import dataclass

from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.registry import PluginKind, PluginRegistry

REFERENCE_DETECTOR_NAME = "reference.hanging_piece"
_HANGING_PIECE_SKILL_ID = "board_awareness.hanging_piece"


@dataclass(frozen=True)
class ReferenceHangingPieceDetector:
    """Reference third-party detector using independent evidence and severity checks."""

    minimum_eval_drop: float = 2.0

    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        evaluation_drop = opportunity.engine_eval_before - opportunity.engine_eval_after
        if evaluation_drop < self.minimum_eval_drop:
            return []

        matches = [item for item in evidence if _is_hanging_piece_signal(item)]
        if not matches:
            return []

        return [
            DiagnosisCandidate(
                skill_id=_HANGING_PIECE_SKILL_ID,
                confidence=_confidence(evaluation_drop, self.minimum_eval_drop),
                evidence_ids=[item.evidence_id for item in matches],
                metadata={"detector": REFERENCE_DETECTOR_NAME},
            )
        ]


def register_reference_detector(registry: PluginRegistry) -> None:
    registry.register(
        kind=PluginKind.PATTERN_DETECTOR,
        name=REFERENCE_DETECTOR_NAME,
        plugin=ReferenceHangingPieceDetector(),
    )


def _is_hanging_piece_signal(evidence: Evidence) -> bool:
    if evidence.kind != "blunder_analysis":
        return False
    return evidence.payload.get("blunder_type") == "hanging_piece_lost" or bool(
        evidence.payload.get("is_hanging_piece_blunder")
    )


def _confidence(evaluation_drop: float, minimum_eval_drop: float) -> float:
    return min(0.95, 0.65 + (evaluation_drop - minimum_eval_drop) * 0.05)
