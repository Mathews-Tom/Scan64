from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.interfaces import PatternDetector


class PromotionGateError(ValueError):
    """Raised when a golden-fixture promotion evaluation is invalid."""


@dataclass(frozen=True)
class GoldenFixture:
    fixture_id: str
    expected_label: str
    opportunity: LearningOpportunity
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class DetectorMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    brier_score: float

    @property
    def precision(self) -> float:
        predictions = self.true_positives + self.false_positives
        return self.true_positives / predictions if predictions else 0.0

    @property
    def recall(self) -> float:
        positives = self.true_positives + self.false_negatives
        return self.true_positives / positives if positives else 0.0


@dataclass(frozen=True)
class PromotionGateResult:
    approved: bool
    candidate: DetectorMetrics
    incumbent: DetectorMetrics
    reasons: tuple[str, ...]


def load_golden_fixtures(
    path: Path, *, player_id: str = "golden-fixture-player"
) -> tuple[GoldenFixture, ...]:
    try:
        raw_fixtures = json.loads(path.read_text())
    except OSError as error:
        raise PromotionGateError(f"Cannot read golden fixture corpus: {path}") from error
    except json.JSONDecodeError as error:
        raise PromotionGateError(f"Golden fixture corpus is not valid JSON: {path}") from error
    if not isinstance(raw_fixtures, list) or not raw_fixtures:
        raise PromotionGateError("Golden fixture corpus must be a non-empty list")

    fixtures: list[GoldenFixture] = []
    for index, raw_fixture in enumerate(raw_fixtures):
        if not isinstance(raw_fixture, dict):
            raise PromotionGateError(f"Golden fixture at index {index} must be an object")
        fixture_id = _required_string(raw_fixture, "id", index)
        expected_label = _required_string(raw_fixture, "expected_label", index)
        mock_evidence = raw_fixture.get("mock_evidence", {})
        if not isinstance(mock_evidence, dict):
            raise PromotionGateError(f"Golden fixture {fixture_id!r} has invalid mock_evidence")
        opportunity_data = mock_evidence.get("opportunity", {})
        if not isinstance(opportunity_data, dict):
            raise PromotionGateError(
                f"Golden fixture {fixture_id!r} has invalid opportunity evidence"
            )
        evidence_data = mock_evidence.get("evidence_list", [])
        if not isinstance(evidence_data, list):
            raise PromotionGateError(f"Golden fixture {fixture_id!r} has invalid evidence_list")
        fixtures.append(
            GoldenFixture(
                fixture_id=fixture_id,
                expected_label=expected_label,
                opportunity=LearningOpportunity(
                    opportunity_id=fixture_id,
                    position_id=fixture_id,
                    player_id=player_id,
                    played_move="e4",
                    engine_eval_before=_numeric(opportunity_data, "engine_eval_before", fixture_id),
                    engine_eval_after=_numeric(opportunity_data, "engine_eval_after", fixture_id),
                ),
                evidence=tuple(
                    _evidence(item, fixture_id, evidence_index)
                    for evidence_index, item in enumerate(evidence_data)
                ),
            )
        )
    return tuple(fixtures)


async def evaluate_detector_promotion(
    *,
    candidate: PatternDetector,
    incumbent: PatternDetector,
    fixtures: tuple[GoldenFixture, ...],
    skill_id: str,
) -> PromotionGateResult:
    if not fixtures:
        raise PromotionGateError("Promotion evaluation requires at least one golden fixture")
    if not skill_id.strip():
        raise PromotionGateError("Promotion evaluation requires a skill ID")

    candidate_metrics = await _evaluate_detector(candidate, fixtures, skill_id)
    incumbent_metrics = await _evaluate_detector(incumbent, fixtures, skill_id)
    reasons = _rejection_reasons(candidate_metrics, incumbent_metrics)
    return PromotionGateResult(
        approved=not reasons,
        candidate=candidate_metrics,
        incumbent=incumbent_metrics,
        reasons=tuple(reasons),
    )


async def _evaluate_detector(
    detector: PatternDetector,
    fixtures: tuple[GoldenFixture, ...],
    skill_id: str,
) -> DetectorMetrics:
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    brier_sum = 0.0

    for fixture in fixtures:
        candidates = await detector.detect(
            fixture.opportunity,
            list(fixture.evidence),
            PlayerContext(player_id=fixture.opportunity.player_id),
        )
        confidence = _prediction_confidence(candidates, skill_id, fixture.fixture_id)
        expected = fixture.expected_label == skill_id
        predicted = confidence is not None
        probability = confidence if confidence is not None else 0.0
        brier_sum += (probability - float(expected)) ** 2
        if predicted and expected:
            true_positives += 1
        elif predicted:
            false_positives += 1
        elif expected:
            false_negatives += 1

    return DetectorMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        brier_score=brier_sum / len(fixtures),
    )


def _prediction_confidence(candidates: list[Any], skill_id: str, fixture_id: str) -> float | None:
    matching = [candidate.confidence for candidate in candidates if candidate.skill_id == skill_id]
    for confidence in matching:
        if not 0.0 <= confidence <= 1.0:
            raise PromotionGateError(
                f"Detector returned out-of-range confidence for fixture {fixture_id!r}"
            )
    return max(matching) if matching else None


def _rejection_reasons(candidate: DetectorMetrics, incumbent: DetectorMetrics) -> list[str]:
    reasons: list[str] = []
    if candidate.precision < incumbent.precision:
        reasons.append("candidate precision regresses")
    if candidate.recall < incumbent.recall:
        reasons.append("candidate recall regresses")
    if candidate.brier_score > incumbent.brier_score:
        reasons.append("candidate calibration regresses")
    if candidate.precision == incumbent.precision and candidate.recall == incumbent.recall:
        reasons.append("candidate does not improve precision or recall")
    return reasons


def _required_string(raw_fixture: dict[str, Any], field: str, index: int) -> str:
    value = raw_fixture.get(field)
    if not isinstance(value, str) or not value:
        raise PromotionGateError(f"Golden fixture at index {index} has invalid {field!r}")
    return value


def _numeric(raw_opportunity: dict[str, Any], field: str, fixture_id: str) -> float:
    value = raw_opportunity.get(field, 0.0)
    if not isinstance(value, int | float):
        raise PromotionGateError(f"Golden fixture {fixture_id!r} has invalid {field!r}")
    return float(value)


def _evidence(raw_evidence: object, fixture_id: str, index: int) -> Evidence:
    if not isinstance(raw_evidence, dict):
        raise PromotionGateError(
            f"Golden fixture {fixture_id!r} has invalid evidence at index {index}"
        )
    kind = raw_evidence.get("kind")
    payload = raw_evidence.get("payload", {})
    if not isinstance(kind, str) or not kind:
        raise PromotionGateError(f"Golden fixture {fixture_id!r} evidence has invalid kind")
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise PromotionGateError(f"Golden fixture {fixture_id!r} evidence has invalid payload")
    return Evidence(
        evidence_id=f"{fixture_id}-evidence-{index}",
        kind=kind,
        position_id=fixture_id,
        engine_analysis_id=f"{fixture_id}-analysis",
        claim="",
        payload=payload,
    )
