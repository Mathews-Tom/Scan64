from __future__ import annotations

from enum import StrEnum

from scan64.chess.opponents.protocols import OpponentPolicy
from scan64.learning.plugins.interfaces import (
    AnalysisProvider,
    ExerciseGenerator,
    ExplanationProvider,
    LessonVerifier,
    PatternDetector,
)


class PluginKind(StrEnum):
    ANALYSIS_PROVIDER = "analysis_provider"
    OPPONENT_POLICY = "opponent_policy"
    PATTERN_DETECTOR = "pattern_detector"
    EXERCISE_GENERATOR = "exercise_generator"
    LESSON_VERIFIER = "lesson_verifier"
    EXPLANATION_PROVIDER = "explanation_provider"


class PluginRegistrationError(ValueError):
    """Raised when a plugin registration violates the extension contract."""


_PLUGIN_INTERFACES: dict[PluginKind, type[object]] = {
    PluginKind.ANALYSIS_PROVIDER: AnalysisProvider,
    PluginKind.OPPONENT_POLICY: OpponentPolicy,
    PluginKind.PATTERN_DETECTOR: PatternDetector,
    PluginKind.EXERCISE_GENERATOR: ExerciseGenerator,
    PluginKind.LESSON_VERIFIER: LessonVerifier,
    PluginKind.EXPLANATION_PROVIDER: ExplanationProvider,
}


class PluginRegistry:
    """Explicit registry for host-selected plugin implementations."""

    def __init__(self) -> None:
        self._plugins: dict[PluginKind, dict[str, object]] = {
            kind: {} for kind in PluginKind
        }

    def register(self, *, kind: PluginKind, name: str, plugin: object) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise PluginRegistrationError("Plugin names must not be blank")
        if normalized_name in self._plugins[kind]:
            raise PluginRegistrationError(
                f"Plugin {normalized_name!r} is already registered for {kind.value}"
            )
        if not isinstance(plugin, _PLUGIN_INTERFACES[kind]):
            raise PluginRegistrationError(
                f"Plugin {normalized_name!r} does not implement {kind.value}"
            )
        self._plugins[kind][normalized_name] = plugin

    def get(self, *, kind: PluginKind, name: str) -> object:
        try:
            return self._plugins[kind][name]
        except KeyError as error:
            raise PluginRegistrationError(
                f"Plugin {name!r} is not registered for {kind.value}"
            ) from error

    def names(self, *, kind: PluginKind) -> tuple[str, ...]:
        return tuple(self._plugins[kind])
