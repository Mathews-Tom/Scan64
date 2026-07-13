import chess


def check_minor_pieces_developed(board: chess.Board, color: chess.Color) -> bool:
    """Check that all minor pieces for the given color have left their starting squares."""
    if color == chess.WHITE:
        starting_squares = [chess.B1, chess.C1, chess.F1, chess.G1]
    else:
        starting_squares = [chess.B8, chess.C8, chess.F8, chess.G8]

    for square in starting_squares:
        piece = board.piece_at(square)
        if piece and piece.color == color and piece.piece_type in (chess.KNIGHT, chess.BISHOP):
            return False
    return True


def check_pawn_tension_maintained(board: chess.Board, color: chess.Color) -> bool:
    """Check that at least one central pawn tension exists."""
    # Central squares: d4, e4, d5, e5 and their adjacent attacking squares c4/f4/c5/f5.
    # We define tension as: a pawn of `color` is attacking an opponent's pawn.
    tension_exists = False
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece and piece.color == color and piece.piece_type == chess.PAWN:
            attacks = board.attacks(square)
            for target_square in attacks:
                target_piece = board.piece_at(target_square)
                if (
                    target_piece
                    and target_piece.color != color
                    and target_piece.piece_type == chess.PAWN
                ):
                    tension_exists = True
                    break
    return tension_exists


def check_solid_pawn_structure(board: chess.Board, color: chess.Color) -> bool:
    """Check that the color has no isolated or doubled pawns."""
    pawn_files: dict[int, list[int]] = {file_idx: [] for file_idx in range(8)}
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece and piece.color == color and piece.piece_type == chess.PAWN:
            pawn_files[chess.square_file(square)].append(chess.square_rank(square))

    # Check for doubled pawns
    for file_idx, ranks in pawn_files.items():
        if len(ranks) > 1:
            return False

    # Check for isolated pawns
    for file_idx, ranks in pawn_files.items():
        if len(ranks) > 0:
            left_isolated = file_idx == 0 or len(pawn_files[file_idx - 1]) == 0
            right_isolated = file_idx == 7 or len(pawn_files[file_idx + 1]) == 0
            if left_isolated and right_isolated:
                return False

    return True


INVARIANT_CHECKERS = {
    "minor_pieces_developed": check_minor_pieces_developed,
    "pawn_tension_maintained": check_pawn_tension_maintained,
    "solid_pawn_structure": check_solid_pawn_structure,
}
