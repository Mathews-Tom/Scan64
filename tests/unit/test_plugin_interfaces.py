from __future__ import annotations

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
