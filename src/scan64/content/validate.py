import argparse
import hashlib
import sys
from pathlib import Path

import chess
import chess.syzygy

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


TABLEBASE_DIRECTORY = Path(__file__).with_name("tablebases")
TABLEBASE_FILE_HASHES = (
    ("KQvK.rtbw", "517667dff787162dbb1ed9d5d6484d30ee854e686ee0675c08d99ecf045d2d50"),
    ("KQvK.rtbz", "71ea9444fa5bd42897d781a0c356975ea6f23e0f65a4254e470897031c161c8c"),
    ("KPvK.rtbw", "02fd2bdcd92821c869458f019f63524def1c9e7a2862ca8d2b16dc7712b16183"),
    ("KPvK.rtbz", "ad01af2076183c90ccee662ca6c5d74da3222ff1fee47c01b5d4e56ed01938de"),
)


def validate_tablebase_solution(
    fen: object,
    solution: object,
    tablebase_directory: Path = TABLEBASE_DIRECTORY,
) -> str | None:
    error = validate_solution_line(fen, solution)
    if error is not None:
        return error

    assert isinstance(fen, str)
    assert isinstance(solution, list)
    moves = tuple(move for move in solution if isinstance(move, str))
    assert len(moves) == len(solution)

    for filename, expected_hash in TABLEBASE_FILE_HASHES:
        table_file = tablebase_directory / filename
        if not table_file.is_file():
            return f"missing Syzygy tablebase file: {filename}"
        with table_file.open("rb") as stream:
            actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_hash != expected_hash:
            return f"unexpected checksum for Syzygy tablebase file: {filename}"

    board = chess.Board(fen)
    try:
        with chess.syzygy.open_tablebase(str(tablebase_directory)) as tablebase:
            root_wdl = tablebase.probe_wdl_table(board)
            for uci in moves:
                board.push_uci(uci)
            final_wdl = tablebase.probe_wdl_table(board)
    except chess.syzygy.MissingTableError as error:
        return f"Syzygy tablebase does not cover solution: {error}"
    except OSError as error:
        return f"could not read Syzygy tablebase: {error}"

    solution_wdl = final_wdl if len(moves) % 2 == 0 else -final_wdl
    if solution_wdl != root_wdl:
        return "solution changes the tablebase WDL outcome"

    return None


def validate_endgames() -> bool:
    from scan64.content.endgames.curated import ENDGAME_PUZZLES

    if not ENDGAME_PUZZLES:
        print("Error: ENDGAME_PUZZLES is empty")
        return False

    for index, puzzle in enumerate(ENDGAME_PUZZLES):
        error = validate_puzzle_metadata(puzzle, index, "endgame puzzle")
        if error is None:
            error = validate_tablebase_solution(puzzle["fen"], puzzle["solution"])
        if error is not None:
            print(f"Error: endgame {puzzle['id']}: {error}")
            return False

    print(f"Successfully validated {len(ENDGAME_PUZZLES)} endgame puzzles.")
    return True


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
    parser = argparse.ArgumentParser(description="Validate curated content sets")
    parser.add_argument(
        "--domain",
        default="all",
        choices=["tactics", "endgames", "all"],
        help="Domain to validate (default: all)",
    )
    args = parser.parse_args()

    success = True
    if args.domain in ["tactics", "all"] and not validate_tactics():
        success = False
    if args.domain in ["endgames", "all"] and not validate_endgames():
        success = False

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
