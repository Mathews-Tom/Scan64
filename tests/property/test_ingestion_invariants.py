import chess
import chess.pgn
from hypothesis import given
from hypothesis import strategies as st

from scan64.chess.games.ingestion import _get_canonical_id, ingest_fen, ingest_pgn


# A basic strategy to generate random legal chess boards
@st.composite
def random_legal_board(draw) -> chess.Board:
    board = chess.Board()
    # Apply 0 to 40 random legal moves
    num_moves = draw(st.integers(min_value=0, max_value=40))
    for _ in range(num_moves):
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            break # Game over
        move = draw(st.sampled_from(legal_moves))
        board.push(move)
    return board

@given(random_legal_board())
def test_ingest_fen_invariant(board: chess.Board):
    fen = board.fen()
    pos = ingest_fen(fen)

    # Invariant: Ingestion must not alter or lose FEN data
    assert pos.fen == fen
    assert pos.half_move_clock == board.halfmove_clock
    assert pos.full_move_number == board.fullmove_number
    assert pos.side_to_move == ('w' if board.turn == chess.WHITE else 'b')
    assert pos.canonical_id == _get_canonical_id(board)

    # If the board has no moves from the initial position, test it as a standard game
    # Otherwise, we create a PGN with a Setup FEN and moves

    game_node = chess.pgn.Game.from_board(board)
    game_node.headers["Event"] = "Hypothesis Test"

    pgn_string = str(game_node)

    game, positions = ingest_pgn(pgn_string)

    # Invariant: The parsed moves must exactly match the board's move stack (from the starting FEN)
    if board.move_stack:
        assert len(game.moves) == len(board.move_stack)
        assert len(positions) == len(board.move_stack) + 1

        # Check that re-applying the parsed moves recreates the same board state
        test_board = chess.Board(positions[0].fen)
        for move_uci in game.moves:
            test_board.push(chess.Move.from_uci(move_uci))

        assert test_board.fen() == board.fen()
