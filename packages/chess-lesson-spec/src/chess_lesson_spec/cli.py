from __future__ import annotations

import argparse
from pathlib import Path

from chess_lesson_spec.conformance import (
    RendererConformanceError,
    RendererLoadError,
    run_conformance,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run published LessonSpec fixtures against a renderer adapter."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run every published conformance fixture.")
    run_parser.add_argument(
        "--renderer",
        required=True,
        type=Path,
        help="Directory containing a renderer.py adapter.",
    )
    arguments = parser.parse_args()

    if arguments.command == "run":
        try:
            report = run_conformance(arguments.renderer)
        except (RendererConformanceError, RendererLoadError) as error:
            parser.error(str(error))
        print(
            f"Conformance passed: {report.fixture_count} fixtures, "
            f"{report.visualization_count} visualization commands."
        )
