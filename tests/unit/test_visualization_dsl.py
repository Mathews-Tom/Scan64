import pytest
from chess_lesson_spec import (
    AnimateLineCommand,
    DrawArrowCommand,
    Explanation,
    HighlightSquareCommand,
    Hint,
)
from pydantic import ValidationError


def test_visualization_commands_require_description():
    with pytest.raises(ValidationError, match="description\n  Field required"):
        HighlightSquareCommand(square="e4")  # type: ignore

    with pytest.raises(ValidationError, match="description\n  Field required"):
        DrawArrowCommand(origin="e2", target="e4")  # type: ignore

    cmd = HighlightSquareCommand(
        description="Highlights the e4 square to indicate the focus of the lesson.",
        square="e4",
    )
    assert cmd.command == "highlight_square"
    assert cmd.square == "e4"
    assert cmd.description == "Highlights the e4 square to indicate the focus of the lesson."


def test_hint_visualizations():
    hint = Hint(
        level=1,
        kind="prompt",
        text="Look at the center.",
        visualizations=[
            HighlightSquareCommand(
                description="Highlight e4",
                square="e4",
            ),
            DrawArrowCommand(
                description="Arrow from e2 to e4",
                origin="e2",
                target="e4",
            ),
        ],
    )
    assert len(hint.visualizations) == 2
    assert hint.visualizations[0].command == "highlight_square"
    assert hint.visualizations[1].command == "draw_arrow"


def test_explanation_visualizations():
    explanation = Explanation(
        text="By moving the pawn, you control the center.",
        visualizations=[
            AnimateLineCommand(
                description="Shows the pawn move and response",
                moves=["e4", "e5"],
            )
        ],
    )
    assert len(explanation.visualizations) == 1
    assert explanation.visualizations[0].command == "animate_line"
