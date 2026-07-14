import chess

from scan64.content.openings.invariants import (
    check_minor_pieces_developed,
    check_pawn_tension_maintained,
    check_solid_pawn_structure,
)


def test_minor_pieces_developed():
    # Initial board: false
    board = chess.Board()
    assert not check_minor_pieces_developed(board, chess.WHITE)
    assert not check_minor_pieces_developed(board, chess.BLACK)

    # White develops all minor pieces
    board_dev = chess.Board("rnbqkbnr/pppppppp/8/8/8/2N2N2/PPPPPPPP/R1BQK2R w KQkq - 0 1")
    # Wait, bishops are still on c1, f1. Let's make a real string where all are developed.
    board_dev = chess.Board("rnbqkbnr/pppppppp/8/8/2B2B2/2N2N2/PPPPPPPP/R2QK2R w KQkq - 0 1")
    assert check_minor_pieces_developed(board_dev, chess.WHITE)
    assert not check_minor_pieces_developed(board_dev, chess.BLACK)


def test_pawn_tension_maintained():
    # Initial board: no tension
    board = chess.Board()
    assert not check_pawn_tension_maintained(board, chess.WHITE)

    # Tension: d4 vs c5 (Sicilian/Queen's gambit style)
    board_tension = chess.Board("rnbqkbnr/pp1ppppp/8/2p5/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 1")
    assert check_pawn_tension_maintained(board_tension, chess.WHITE)
    assert check_pawn_tension_maintained(board_tension, chess.BLACK)


def test_solid_pawn_structure():
    # Initial board: solid
    board = chess.Board()
    assert check_solid_pawn_structure(board, chess.WHITE)

    # Doubled pawns
    board_doubled = chess.Board("rnbqkbnr/pppppppp/8/8/8/P7/P1PPPPPP/RNBQKBNR w KQkq - 0 1")
    assert not check_solid_pawn_structure(board_doubled, chess.WHITE)

    # Isolated pawn
    board_isolated = chess.Board("rnbqkbnr/pppppppp/8/8/3P4/8/P1P1PPPP/RNBQKBNR w KQkq - 0 1")
    assert not check_solid_pawn_structure(board_isolated, chess.WHITE)
