from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from chess_lesson_spec import Diagnosis, Explanation, LessonSpec

from scan64.chess.analysis.models import EngineAnalysis, EngineAnalysisConfig
from scan64.chess.positions.models import Position
from scan64.explanations.validator import GroundedExplanationContext
from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import SkillState
from scan64.providers.llm.contracts import ExplanationRequest


type AnalysisRequest = EngineAnalysisConfig
type AnalysisResult = EngineAnalysis
type PlayerState = SkillState
type ExerciseCandidate = LessonSpec
type ExplanationEvidence = GroundedExplanationContext
type ExplanationPolicy = ExplanationRequest
type GroundedExplanation = Explanation


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    errors: tuple[str, ...] = ()


@runtime_checkable
class AnalysisProvider(Protocol):
    async def analyse(self, position: Position, request: AnalysisRequest) -> AnalysisResult: ...


@runtime_checkable
class PatternDetector(Protocol):
    async def detect(
        self,
        opportunity: LearningOpportunity,
        evidence: list[Evidence],
        player_context: PlayerContext,
    ) -> list[DiagnosisCandidate]: ...


@runtime_checkable
class ExerciseGenerator(Protocol):
    async def generate(
        self,
        diagnosis: Diagnosis,
        player_state: PlayerState,
    ) -> list[ExerciseCandidate]: ...


@runtime_checkable
class LessonVerifier(Protocol):
    async def verify(self, candidate: LessonSpec) -> VerificationResult: ...


@runtime_checkable
class ExplanationProvider(Protocol):
    async def explain(
        self,
        evidence: ExplanationEvidence,
        policy: ExplanationPolicy,
    ) -> GroundedExplanation: ...
