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
