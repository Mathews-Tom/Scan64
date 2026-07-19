from __future__ import annotations

import pytest
from chess_lesson_spec import Diagnosis, Explanation, LessonSpec

from scan64.chess.analysis.models import EngineAnalysis, EngineAnalysisConfig
from scan64.chess.opponents.protocols import MoveDecision, OpponentContext
from scan64.chess.positions.models import Position
from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins import (
    AnalysisProvider,
    ExerciseGenerator,
    ExplanationProvider,
    LessonVerifier,
    OpponentPolicy,
    PatternDetector,
    PluginKind,
    PluginRegistrationError,
    PluginRegistry,
    VerificationResult,
)
from scan64.learning.plugins.interfaces import (
    ExplanationEvidence,
    ExplanationPolicy,
    PlayerState,
)
from scan64.learning.plugins.reference_detector import (
    REFERENCE_DETECTOR_NAME,
    ReferenceHangingPieceDetector,
    register_reference_detector,
)


class ExampleAnalysisProvider:
    async def analyse(self, position: Position, request: EngineAnalysisConfig) -> EngineAnalysis:
        raise AssertionError("The protocol contract is not invoked in this test")


class ExamplePatternDetector:
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]:
        return []


class ExampleExerciseGenerator:
    async def generate(self, diagnosis: Diagnosis, player_state: PlayerState) -> list[LessonSpec]:
        return []


class ExampleLessonVerifier:
    async def verify(self, candidate: LessonSpec) -> VerificationResult:
        return VerificationResult(verified=True)


class ExampleExplanationProvider:
    async def explain(
        self,
        evidence: ExplanationEvidence,
        policy: ExplanationPolicy,
    ) -> Explanation:
        raise AssertionError("The protocol contract is not invoked in this test")


class ExampleOpponentPolicy:
    async def choose_move(self, position: Position, context: OpponentContext) -> MoveDecision:
        raise AssertionError("The protocol contract is not invoked in this test")


def test_plugin_protocols_are_runtime_checkable_extension_contracts() -> None:
    assert isinstance(ExampleAnalysisProvider(), AnalysisProvider)
    assert isinstance(ExamplePatternDetector(), PatternDetector)
    assert isinstance(ExampleExerciseGenerator(), ExerciseGenerator)
    assert isinstance(ExampleLessonVerifier(), LessonVerifier)
    assert isinstance(ExampleExplanationProvider(), ExplanationProvider)
    assert isinstance(ExampleOpponentPolicy(), OpponentPolicy)


def test_plugin_registration_accepts_a_conforming_plugin() -> None:
    registry = PluginRegistry()
    detector = ExamplePatternDetector()

    registry.register(
        kind=PluginKind.PATTERN_DETECTOR,
        name="third_party.example_detector",
        plugin=detector,
    )

    assert registry.get(
        kind=PluginKind.PATTERN_DETECTOR,
        name="third_party.example_detector",
    ) is detector
    assert registry.names(kind=PluginKind.PATTERN_DETECTOR) == ("third_party.example_detector",)


def test_plugin_registration_rejects_invalid_or_duplicate_plugins() -> None:
    registry = PluginRegistry()
    detector = ExamplePatternDetector()

    with pytest.raises(PluginRegistrationError, match="must not be blank"):
        registry.register(kind=PluginKind.PATTERN_DETECTOR, name="  ", plugin=detector)
    with pytest.raises(PluginRegistrationError, match="does not implement"):
        registry.register(
            kind=PluginKind.PATTERN_DETECTOR,
            name="third_party.invalid_detector",
            plugin=object(),
        )

    registry.register(
        kind=PluginKind.PATTERN_DETECTOR,
        name="third_party.example_detector",
        plugin=detector,
    )

    with pytest.raises(PluginRegistrationError, match="already registered"):
        registry.register(
            kind=PluginKind.PATTERN_DETECTOR,
            name="third_party.example_detector",
            plugin=detector,
        )


@pytest.mark.asyncio
async def test_reference_detector_registers_through_plugin_interface() -> None:
    registry = PluginRegistry()

    register_reference_detector(registry)

    detector = registry.get(
        kind=PluginKind.PATTERN_DETECTOR,
        name=REFERENCE_DETECTOR_NAME,
    )
    assert isinstance(detector, PatternDetector)
    candidates = await detector.detect(
        LearningOpportunity(
            opportunity_id="opportunity-1",
            position_id="position-1",
            player_id="player-1",
            played_move="e4",
            engine_eval_before=3.0,
            engine_eval_after=-1.0,
        ),
        [
            Evidence(
                evidence_id="evidence-1",
                kind="blunder_analysis",
                position_id="position-1",
                engine_analysis_id="analysis-1",
                claim="The bishop was left en prise.",
                payload={"is_hanging_piece_blunder": True},
            )
        ],
        PlayerContext(player_id="player-1"),
    )

    assert len(candidates) == 1
    assert candidates[0].skill_id == "board_awareness.hanging_piece"
    assert candidates[0].confidence == 0.75
    assert candidates[0].metadata["detector"] == REFERENCE_DETECTOR_NAME


@pytest.mark.asyncio
async def test_reference_detector_uses_its_configured_evaluation_drop_floor() -> None:
    detector = ReferenceHangingPieceDetector(minimum_eval_drop=1.0)

    candidates = await detector.detect(
        LearningOpportunity(
            opportunity_id="opportunity-2",
            position_id="position-2",
            player_id="player-1",
            played_move="e4",
            engine_eval_before=2.0,
            engine_eval_after=1.0,
        ),
        [
            Evidence(
                evidence_id="evidence-2",
                kind="blunder_analysis",
                position_id="position-2",
                engine_analysis_id="analysis-2",
                claim="The knight was left en prise.",
                payload={"blunder_type": "hanging_piece_lost"},
            )
        ],
        PlayerContext(player_id="player-1"),
    )

    assert len(candidates) == 1
    assert candidates[0].confidence == 0.65


@pytest.mark.asyncio
async def test_reference_detector_rejects_insufficient_or_unrelated_evidence() -> None:
    detector = ReferenceHangingPieceDetector()
    player_context = PlayerContext(player_id="player-1")
    hanging_piece_evidence = [
        Evidence(
            evidence_id="evidence-3",
            kind="blunder_analysis",
            position_id="position-3",
            engine_analysis_id="analysis-3",
            claim="A piece was left en prise.",
            payload={"is_hanging_piece_blunder": True},
        )
    ]

    insufficient = await detector.detect(
        LearningOpportunity(
            opportunity_id="opportunity-3",
            position_id="position-3",
            player_id="player-1",
            played_move="e4",
            engine_eval_before=2.0,
            engine_eval_after=1.0,
        ),
        hanging_piece_evidence,
        player_context,
    )
    unrelated = await detector.detect(
        LearningOpportunity(
            opportunity_id="opportunity-4",
            position_id="position-4",
            player_id="player-1",
            played_move="e4",
            engine_eval_before=4.0,
            engine_eval_after=0.0,
        ),
        [
            Evidence(
                evidence_id="evidence-4",
                kind="engine_analysis",
                position_id="position-4",
                engine_analysis_id="analysis-4",
                claim="No hanging piece signal exists.",
                payload={},
            )
        ],
        player_context,
    )

    assert insufficient == []
    assert unrelated == []
