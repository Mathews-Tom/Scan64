import argparse
import sys

import chess

CONTENT_KEYS = (
    "id",
    "fen",
    "solution",
    "provenance",
    "licence",
    "skill_mapping",
    "difficulty_estimate",
)


def validate_puzzle_metadata(puzzle: dict[str, object], index: int, label: str) -> str | None:
    for key in CONTENT_KEYS:
        if key not in puzzle:
            return f"{label} {index} missing required key '{key}'"

    for key in ("id", "provenance", "licence"):
        value = puzzle[key]
        if not isinstance(value, str) or not value.strip():
            return f"{label} {index} has an invalid {key}"

    skill_mapping = puzzle["skill_mapping"]
    if not isinstance(skill_mapping, dict) or not skill_mapping:
        return f"{label} {index} has an empty skill_mapping"
    if not all(
        isinstance(skill, str)
        and skill
        and isinstance(weight, int | float)
        and not isinstance(weight, bool)
        and weight > 0
        for skill, weight in skill_mapping.items()
    ):
        return f"{label} {index} has an invalid skill_mapping"

    difficulty = puzzle["difficulty_estimate"]
    if not isinstance(difficulty, int | float) or isinstance(difficulty, bool) or difficulty <= 0:
        return f"{label} {index} has an invalid difficulty_estimate"

    return None


def validate_solution_line(fen: object, solution: object) -> str | None:
    if not isinstance(fen, str):
        return "FEN must be a string"
    if (
        not isinstance(solution, list)
        or not solution
        or not all(isinstance(move, str) for move in solution)
    ):
        return "solution must be a non-empty list of UCI moves"

    try:
        board = chess.Board(fen)
    except ValueError as error:
        return f"invalid FEN: {error}"

    if not board.is_valid():
        return "invalid chess position"

    for ply, uci in enumerate(solution, start=1):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as error:
            return f"invalid UCI move at ply {ply}: {error}"
        if move not in board.legal_moves:
            return f"illegal UCI move at ply {ply}: {uci}"
        board.push(move)

    return None


def validate_tactics() -> bool:
    from scan64.content.tactics.curated import TACTICS_PUZZLES

    if not TACTICS_PUZZLES:
        print("Error: TACTICS_PUZZLES is empty")
        return False

    for index, puzzle in enumerate(TACTICS_PUZZLES):
        error = validate_puzzle_metadata(puzzle, index, "tactics puzzle")
        if error is None:
            error = validate_solution_line(puzzle["fen"], puzzle["solution"])
        if error is not None:
            print(f"Error: {error}")
            return False

    print(f"Successfully validated {len(TACTICS_PUZZLES)} tactics puzzles.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate curated tactics")
    parser.add_argument(
        "--domain",
        default="tactics",
        choices=["tactics"],
        help="Domain to validate",
    )
    parser.parse_args()

    if not validate_tactics():
        sys.exit(1)


if __name__ == "__main__":
    main()
