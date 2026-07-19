from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import cast

from chess_lesson_spec.fixtures import fixture_paths
from chess_lesson_spec.models import LessonSpec, VisualizationCommand

type RenderVisualization = Callable[[dict[str, object]], None]


class RendererLoadError(RuntimeError):
    """Raised when a renderer directory does not expose the required adapter."""


class RendererConformanceError(RuntimeError):
    """Raised when a renderer cannot handle a published fixture command."""


@dataclass(frozen=True)
class ConformanceReport:
    fixture_count: int
    visualization_count: int


def run_conformance(renderer_directory: Path) -> ConformanceReport:
    """Run every published LessonSpec fixture through a renderer adapter."""
    render_visualization = _load_renderer(renderer_directory)
    fixtures = fixture_paths()
    visualization_count = 0

    for fixture_path in fixtures:
        lesson = LessonSpec.model_validate_json(fixture_path.read_text(encoding="utf-8"))
        for command in _visualizations(lesson):
            visualization_count += 1
            try:
                render_visualization(cast(dict[str, object], command.model_dump(mode="json")))
            except NotImplementedError as error:
                raise RendererConformanceError(
                    f"{fixture_path.name}: renderer does not support `{command.command}`."
                ) from error
            except Exception as error:
                raise RendererConformanceError(
                    f"{fixture_path.name}: renderer failed handling `{command.command}`: {error}"
                ) from error

    return ConformanceReport(
        fixture_count=len(fixtures),
        visualization_count=visualization_count,
    )


def _load_renderer(renderer_directory: Path) -> RenderVisualization:
    if not renderer_directory.is_dir():
        raise RendererLoadError(f"Renderer directory does not exist: {renderer_directory}")

    renderer_module_path = renderer_directory / "renderer.py"
    if not renderer_module_path.is_file():
        raise RendererLoadError(f"Renderer adapter is missing: {renderer_module_path}")

    module = _load_module(renderer_module_path)
    candidate = getattr(module, "render_visualization", None)
    if not callable(candidate):
        message = (
            "Renderer adapter must define render_visualization(command: dict[str, object]): "
            f"{renderer_module_path}"
        )
        raise RendererLoadError(message)
    return cast(RenderVisualization, candidate)


def _load_module(renderer_module_path: Path) -> ModuleType:
    specification = spec_from_file_location("scan64_conformance_renderer", renderer_module_path)
    if specification is None or specification.loader is None:
        raise RendererLoadError(f"Unable to load renderer adapter: {renderer_module_path}")

    module = module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _visualizations(lesson: LessonSpec) -> Iterator[VisualizationCommand]:
    for hint in lesson.hints:
        yield from hint.visualizations
    if lesson.explanation is not None:
        yield from lesson.explanation.visualizations
