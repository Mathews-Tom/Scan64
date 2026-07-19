from __future__ import annotations

SUPPORTED_COMMANDS = frozenset(
    {
        "animate_line",
        "compare_positions",
        "dim_irrelevant_pieces",
        "draw_arrow",
        "draw_attack_map",
        "flip_board",
        "hide_coordinates",
        "highlight_piece",
        "highlight_region",
        "highlight_square",
        "show_ghost_piece",
        "temporarily_hide_pieces",
    }
)


def render_visualization(command: dict[str, object]) -> None:
    command_name = command.get("command")
    if not isinstance(command_name, str):
        raise ValueError("Visualization command is missing a string command name.")
    if command_name not in SUPPORTED_COMMANDS:
        raise NotImplementedError(command_name)
