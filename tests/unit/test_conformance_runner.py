from __future__ import annotations

from pathlib import Path

import pytest
from chess_lesson_spec.conformance import RendererLoadError, run_conformance


def test_runner_invokes_renderer_for_every_fixture_visualization(tmp_path: Path) -> None:
    renderer_source = '''
from pathlib import Path


def render_visualization(command: dict[str, object]) -> None:
    command_name = str(command["command"])
    with (Path(__file__).parent / "commands.txt").open("a", encoding="utf-8") as output:
        output.write(f"{command_name}\\n")
'''
    (tmp_path / "renderer.py").write_text(renderer_source, encoding="utf-8")

    report = run_conformance(tmp_path)

    command_names = (tmp_path / "commands.txt").read_text(encoding="utf-8").splitlines()
    assert report.fixture_count == 3
    assert report.visualization_count == 15
    assert command_names.count("highlight_region") == 2
    assert set(command_names) == {
        "animate_line",
        "compare_positions",
        "dim_irrelevant_pieces",
        "draw_arrow",
        "draw_attack_map",
        "draw_defence_map",
        "flip_board",
        "hide_coordinates",
        "highlight_piece",
        "highlight_region",
        "highlight_square",
        "show_ghost_piece",
        "temporarily_hide_pieces",
    }


def test_runner_requires_renderer_adapter(tmp_path: Path) -> None:
    (tmp_path / "renderer.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(RendererLoadError, match="render_visualization"):
        run_conformance(tmp_path)
