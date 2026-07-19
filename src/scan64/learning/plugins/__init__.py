from scan64.chess.opponents.protocols import OpponentPolicy
from scan64.learning.plugins.interfaces import (
    AnalysisProvider,
    AnalysisRequest,
    AnalysisResult,
    ExerciseCandidate,
    ExerciseGenerator,
    ExplanationEvidence,
    ExplanationPolicy,
    ExplanationProvider,
    GroundedExplanation,
    LessonVerifier,
    PatternDetector,
    PlayerState,
    VerificationResult,
)
from scan64.learning.plugins.registry import PluginKind, PluginRegistrationError, PluginRegistry

__all__ = [
    "AnalysisProvider",
    "AnalysisRequest",
    "AnalysisResult",
    "ExerciseCandidate",
    "ExerciseGenerator",
    "ExplanationEvidence",
    "ExplanationPolicy",
    "ExplanationProvider",
    "GroundedExplanation",
    "LessonVerifier",
    "OpponentPolicy",
    "PatternDetector",
    "PlayerState",
    "VerificationResult",
    "PluginKind",
    "PluginRegistrationError",
    "PluginRegistry",
]
