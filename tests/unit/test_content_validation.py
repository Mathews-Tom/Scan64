from pathlib import Path

import chess

from scan64.content.endgames.curated import ENDGAME_PUZZLES
from scan64.content.tactics.curated import TACTICS_PUZZLES
from scan64.content.validate import (
    validate_endgames,
    validate_solution_line,
    validate_tablebase_solution,
    validate_tactics,
)


def replay_solution(fen: str, solution: list[str]) -> chess.Board:
    board = chess.Board(fen)
    for uci in solution:
        board.push_uci(uci)
    return board


def test_validate_solution_line_accepts_legal_sequence() -> None:
    assert validate_solution_line("6k1/5ppp/8/8/8/8/6PP/3Q2K1 w - - 0 1", ["d1d8"]) is None


def test_validate_solution_line_rejects_invalid_position() -> None:
    assert (
        validate_solution_line("8/8/8/8/8/4k3/3P4/4K3 w - - 0 1", ["e1e2"])
        == "invalid chess position"
    )


def test_validate_solution_line_rejects_illegal_move() -> None:
    assert (
        validate_solution_line("6k1/5ppp/8/8/8/8/6PP/3Q2K1 w - - 0 1", ["d1e3"])
        == "illegal UCI move at ply 1: d1e3"
    )


def test_tactics_catalog_contains_a_forced_mate_and_knight_fork() -> None:
    mate_board = replay_solution(TACTICS_PUZZLES[0]["fen"], TACTICS_PUZZLES[0]["solution"])
    fork_board = replay_solution(TACTICS_PUZZLES[1]["fen"], TACTICS_PUZZLES[1]["solution"])

    assert mate_board.is_checkmate()
    assert chess.D8 in fork_board.attacks(chess.F7)
    assert fork_board.is_check()
    assert validate_tactics()


def test_endgame_catalog_is_tablebase_verified() -> None:
    for puzzle in ENDGAME_PUZZLES:
        assert validate_tablebase_solution(puzzle["fen"], puzzle["solution"]) is None
    assert validate_endgames()


def test_tablebase_validation_rejects_wdl_regression() -> None:
    assert (
        validate_tablebase_solution("k7/8/1QK5/8/8/8/8/8 w - - 0 1", ["b6c7"])
        == "solution changes the tablebase WDL outcome"
    )


def test_tablebase_validation_rejects_missing_data(tmp_path: Path) -> None:
    assert (
        validate_tablebase_solution("7k/8/8/8/8/8/Q3K3/8 w - - 0 1", ["e2e3"], tmp_path)
        == "missing Syzygy tablebase file: KQvK.rtbw"
    )


def test_tablebase_validation_rejects_corrupt_data(tmp_path: Path) -> None:
    (tmp_path / "KQvK.rtbw").write_bytes(b"corrupt")

    assert (
        validate_tablebase_solution("7k/8/8/8/8/8/Q3K3/8 w - - 0 1", ["e2e3"], tmp_path)
        == "unexpected checksum for Syzygy tablebase file: KQvK.rtbw"
    )
