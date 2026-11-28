"""Render a FEN as the plain position the narrator reads.

The application parses the FEN; the model never sees one.
"""

import chess

NAMES = {
    chess.KING: "king",
    chess.QUEEN: "queen",
    chess.ROOK: "rook",
    chess.BISHOP: "bishop",
    chess.KNIGHT: "knight",
    chess.PAWN: "pawn",
}
ORDER = (chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def render(fen: str) -> str:
    """One line per side ("White: king on e1; ..."), then the side to move."""
    board = chess.Board(fen)
    lines = []
    for color, side in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
        parts = []
        for piece_type in ORDER:
            squares = [chess.square_name(s) for s in sorted(board.pieces(piece_type, color))]
            if squares:
                name = NAMES[piece_type] + ("s" if len(squares) > 1 else "")
                parts.append(f"{name} on {_join(squares)}")
        lines.append(f"{side}: " + "; ".join(parts) + ".")
    lines.append(("White" if board.turn == chess.WHITE else "Black") + " to move.")
    return "\n".join(lines)
