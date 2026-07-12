import io
from uuid import UUID, uuid4

import chess
import chess.pgn

from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position


class InvalidGameError(Exception):
    pass

class NonStandardVariantError(InvalidGameError):
    pass

def _get_canonical_id(board: chess.Board) -> str:
    # Epd without move counters but keeps castling and en passant
    return board.epd()

def ingest_fen(fen: str, game_id: UUID | None = None) -> Position:
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise InvalidGameError(f"Invalid FEN: {e}")

    if board.chess960:
        raise NonStandardVariantError("Chess960 is not supported")
    if not isinstance(board, chess.Board) or type(board) is not chess.Board:
        raise NonStandardVariantError("Only standard chess is supported")

    return Position(
        game_id=game_id,
        fen=board.fen(),
        half_move_clock=board.halfmove_clock,
        full_move_number=board.fullmove_number,
        side_to_move='w' if board.turn == chess.WHITE else 'b',
        canonical_id=_get_canonical_id(board)
    )

def ingest_pgn(pgn_string: str) -> tuple[Game, list[Position]]:
    pgn_io = io.StringIO(pgn_string)
    game_node = chess.pgn.read_game(pgn_io)

    if game_node is None:
        raise InvalidGameError("Empty or unparsable PGN")

    # Check for variants in headers
    variant = game_node.headers.get("Variant", "Standard")
    if variant != "Standard":
        raise NonStandardVariantError(f"Variant '{variant}' is not supported")

    headers = dict(game_node.headers)

    game_id = uuid4()

    # Replay game to get moves and validate legality
    board = game_node.board()
    if board.chess960:
        raise NonStandardVariantError("Chess960 is not supported")

    moves = []
    positions = []

    # Store the initial position
    positions.append(Position(
        game_id=game_id,
        fen=board.fen(),
        half_move_clock=board.halfmove_clock,
        full_move_number=board.fullmove_number,
        side_to_move='w' if board.turn == chess.WHITE else 'b',
        canonical_id=_get_canonical_id(board)
    ))

    # If the PGN has setup fen but no moves it will just be the initial pos
    for move in game_node.mainline_moves():
        if not board.is_legal(move):
            raise InvalidGameError(f"Illegal move {move.uci()} in PGN")

        moves.append(move.uci())
        board.push(move)

        positions.append(Position(
            game_id=game_id,
            fen=board.fen(),
            half_move_clock=board.halfmove_clock,
            full_move_number=board.fullmove_number,
            side_to_move='w' if board.turn == chess.WHITE else 'b',
            canonical_id=_get_canonical_id(board)
        ))

    # Check if there are any errors in parsing
    if game_node.errors:
        raise InvalidGameError(f"PGN parsing errors: {game_node.errors}")

    game = Game(
        id=game_id,
        pgn=pgn_string,
        headers=headers,
        moves=moves,
        white=headers.get("White", "Unknown"),
        black=headers.get("Black", "Unknown"),
        result=headers.get("Result", "*"),
        date=headers.get("Date", None)
    )

    return game, positions
