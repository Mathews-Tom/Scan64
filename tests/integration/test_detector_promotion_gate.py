from __future__ import annotations

from pathlib import Path

import pytest

from scan64.learning.diagnosis.detectors.board_awareness import HangingPieceDetector
from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.promotion import (
    evaluate_detector_promotion,
    load_golden_fixtures,
)
from scan64.learning.plugins.reference_detector import ReferenceHangingPieceDetector

_FIXTURE_PATH = Path(__file__).parents[2] / "benchmarks" / "fixtures" / "golden_corpus.json"
_SKILL_ID = "board_awareness.hanging_piece"


class NoisyHangingPieceDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        return [
            DiagnosisCandidate(
                skill_id=_SKILL_ID,
                confidence=1.0,
                evidence_ids=[],
            )
        ]


class PreciseHangingPieceDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        evidence_ids = [
            item.evidence_id
            for item in evidence
            if item.kind == "blunder_analysis"
            and bool(item.payload.get("is_hanging_piece_blunder"))
        ]
        if not evidence_ids:
            return []
        return [
            DiagnosisCandidate(
                skill_id=_SKILL_ID,
                confidence=1.0,
                evidence_ids=evidence_ids,
            )
        ]


class DeliberatelyWorseSampleDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        if opportunity.opportunity_id == "pos_1":
            return []
        return [
            DiagnosisCandidate(
                skill_id=_SKILL_ID,
                confidence=0.95,
                evidence_ids=[],
            )
        ]


class SilentHangingPieceDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        return []


class ImprovedButMiscalibratedDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        if opportunity.opportunity_id not in {"pos_1", "pos_2", "pos_3"}:
            return []
        return [
            DiagnosisCandidate(
                skill_id=_SKILL_ID,
                confidence=1.0,
                evidence_ids=[],
            )
        ]


@pytest.mark.asyncio
async def test_promotion_gate_allows_a_detector_that_improves_golden_fixture_precision() -> None:
    result = await evaluate_detector_promotion(
        candidate=PreciseHangingPieceDetector(),
        incumbent=NoisyHangingPieceDetector(),
        fixtures=load_golden_fixtures(_FIXTURE_PATH),
        skill_id=_SKILL_ID,
    )

    assert result.approved is True
    assert result.candidate.precision > result.incumbent.precision
    assert result.candidate.recall == result.incumbent.recall
    assert result.candidate.brier_score <= result.incumbent.brier_score


@pytest.mark.asyncio
async def test_reference_detector_cannot_replace_incumbent_without_golden_fixture_gain() -> None:
    result = await evaluate_detector_promotion(
        candidate=ReferenceHangingPieceDetector(),
        incumbent=HangingPieceDetector(),
        fixtures=load_golden_fixtures(_FIXTURE_PATH),
        skill_id=_SKILL_ID,
    )

    assert result.approved is False
    assert "candidate calibration regresses" in result.reasons
    assert "candidate does not improve precision or recall" in result.reasons


@pytest.mark.asyncio
async def test_promotion_gate_blocks_improved_detection_with_worse_calibration() -> None:
    result = await evaluate_detector_promotion(
        candidate=ImprovedButMiscalibratedDetector(),
        incumbent=SilentHangingPieceDetector(),
        fixtures=load_golden_fixtures(_FIXTURE_PATH),
        skill_id=_SKILL_ID,
    )

    assert result.approved is False
    assert result.candidate.precision > result.incumbent.precision
    assert result.candidate.recall > result.incumbent.recall
    assert result.candidate.brier_score > result.incumbent.brier_score
    assert result.reasons == ("candidate calibration regresses",)


@pytest.mark.asyncio
async def test_promotion_gate_blocks_deliberately_worse_sample_detector() -> None:
    result = await evaluate_detector_promotion(
        candidate=DeliberatelyWorseSampleDetector(),
        incumbent=HangingPieceDetector(),
        fixtures=load_golden_fixtures(_FIXTURE_PATH),
        skill_id=_SKILL_ID,
    )

    assert result.approved is False
    assert result.candidate.precision < result.incumbent.precision
    assert result.candidate.recall < result.incumbent.recall
    assert "candidate precision regresses" in result.reasons
    assert "candidate recall regresses" in result.reasons
