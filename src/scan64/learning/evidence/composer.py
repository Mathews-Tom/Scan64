from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from chess import (
    BISHOP,
    KING,
    KNIGHT,
    PAWN,
    QUEEN,
    ROOK,
    SQUARES,
    Board,
    Color,
    Move,
    Piece,
    square_name,
)

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.boards import board_from
from scan64.learning.evidence.models import Evidence

_PIECE_VALUES = {
    PAWN: 1,
    KNIGHT: 3,
    BISHOP: 3,
    ROOK: 5,
    QUEEN: 9,
}


def _legal_pv_move(board: Board, analysis: EngineAnalysis) -> Move | None:
    if not analysis.raw_result:
        return None
    pv = analysis.raw_result[0].get("pv")
    if not isinstance(pv, list) or not pv or not isinstance(pv[0], str):
        return None
    try:
        move = Move.from_uci(pv[0])
    except ValueError:
        return None
    return move if move in board.legal_moves else None


def _pv_line(analysis: EngineAnalysis) -> list[str]:
    if not analysis.raw_result:
        return []
    pv = analysis.raw_result[0].get("pv")
    if not isinstance(pv, list) or not all(isinstance(move, str) for move in pv):
        return []
    return pv


def _piece_value(piece: Piece) -> int:
    return _PIECE_VALUES.get(piece.piece_type, 0)


def _evidence(
    *,
    kind: str,
    position_id: str,
    engine_analysis_id: str,
    claim: str,
    payload: dict[str, Any],
) -> Evidence:
    return Evidence(
        evidence_id=f"ev_{uuid4()}",
        kind=kind,
        position_id=position_id,
        engine_analysis_id=engine_analysis_id,
        claim=claim,
        payload=payload,
    )


def _hanging_piece_payload(board: Board, mover: Color) -> dict[str, Any] | None:
    opponent = not mover
    for square in SQUARES:
        piece = board.piece_at(square)
        if (
            piece is None
            or piece.color != mover
            or piece.piece_type == KING
            or not board.is_attacked_by(opponent, square)
            or board.is_attacked_by(mover, square)
        ):
            continue
        return {
            "blunder_type": "hanging_piece_lost",
            "hanging_square": square_name(square),
            "hanging_piece": piece.symbol(),
            "is_hanging_piece_blunder": True,
        }
    return None


def _knight_fork_payload(board: Board, move: Move) -> dict[str, Any] | None:
    moving_piece = board.piece_at(move.from_square)
    if moving_piece is None or moving_piece.piece_type != KNIGHT:
        return None
    board.push(move)
    targets = []
    for square in board.attacks(move.to_square):
        target = board.piece_at(square)
        if target is None or target.color == moving_piece.color or target.piece_type == KING:
            continue
        if not board.is_attacked_by(target.color, move.to_square) or _piece_value(target) > 3:
            targets.append({"square": square_name(square), "piece": target.symbol()})
    if len(targets) < 2:
        return None
    return {
        "tactic_type": "knight_fork",
        "fork_square": square_name(move.to_square),
        "targets": targets,
        "results_in_material_gain": True,
    }


def _pin_payload(board: Board, move: Move) -> dict[str, Any] | None:
    moving_color = board.turn
    board.push(move)
    pinned_color = board.turn
    for square, piece in board.piece_map().items():
        if (
            piece.color == pinned_color
            and piece.piece_type != KING
            and board.is_pinned(pinned_color, square)
        ):
            return {
                "tactic_type": "pin",
                "pinned_square": square_name(square),
                "pinned_piece": piece.symbol(),
                "pinning_move": move.uci(),
                "wins_material": _piece_value(piece) >= 3,
                "pinning_color": "white" if moving_color else "black",
            }
    return None


def _overload_payload(board: Board, move: Move) -> dict[str, Any] | None:
    board.push(move)
    defender_color = board.turn
    attacker_color = not defender_color
    for square, defender in board.piece_map().items():
        if defender.color != defender_color or defender.piece_type == KING:
            continue
        defended_targets = []
        for target_square in board.attacks(square):
            target = board.piece_at(target_square)
            if target is None or target.color != defender_color:
                continue
            if board.is_attacked_by(attacker_color, target_square):
                defended_targets.append(
                    {
                        "square": square_name(target_square),
                        "piece": target.symbol(),
                    }
                )
        if len(defended_targets) >= 2:
            return {
                "tactic_type": "overloaded_defender",
                "defender_square": square_name(square),
                "defender_piece": defender.symbol(),
                "defended_targets": defended_targets,
                "wins_material": True,
            }
    return None


def _opening_payload(
    history_san: list[str],
    player_color: Color,
    initial_fen: str | None,
) -> dict[str, Any] | None:
    if len(history_san) > 20:
        return None
    board = board_from(initial_fen)
    pawn_moves = 0
    developed_minor_pieces = 0
    moved_pieces: dict[int, int] = {}
    repeats = 0
    for san in history_san:
        move = board.parse_san(san)
        if board.turn == player_color:
            piece = board.piece_at(move.from_square)
            if piece is not None:
                if piece.piece_type == PAWN:
                    pawn_moves += 1
                prior_moves = moved_pieces.pop(move.from_square, 0)
                moved_pieces[move.to_square] = prior_moves + 1
                if prior_moves:
                    repeats += 1
                if piece.piece_type in (KNIGHT, BISHOP) and prior_moves == 0:
                    developed_minor_pieces += 1
        board.push(move)
    tempo_loss = float(pawn_moves + repeats - developed_minor_pieces)
    if tempo_loss <= 1.5:
        return None
    return {
        "issue": "delayed_development",
        "tempo_loss": tempo_loss,
        "pawn_moves": pawn_moves,
        "repeated_piece_moves": repeats,
        "minor_pieces_developed": developed_minor_pieces,
    }


def compose_candidate_evidence(
    *,
    before_board: Board,
    after_board: Board,
    history_san: list[str],
    initial_fen: str | None,
    position_id: str,
    fast_analysis: EngineAnalysis,
    focused_analysis: EngineAnalysis,
    played_move: str,
    swing_cp: int,
    analysis_depth: Literal["deep", "interactive"] = "deep",
) -> list[Evidence]:
    """Derive detector inputs exclusively from legal board and engine state."""
    analysis_label = "deep MultiPV" if analysis_depth == "deep" else "bounded interactive"
    focused_line = _pv_line(focused_analysis)
    provenance: dict[str, Any] = {
        "played_move": played_move,
        "fast_analysis_id": str(fast_analysis.id),
        "focused_analysis_id": str(focused_analysis.id),
        "focused_multipv": focused_analysis.raw_result,
        "focused_line": focused_line,
        "swing_cp": swing_cp,
        "analysis_depth": analysis_depth,
    }
    engine_analysis_id = str(focused_analysis.id)
    evidence = [
        _evidence(
            kind="engine_analysis",
            position_id=position_id,
            engine_analysis_id=engine_analysis_id,
            claim=f"{analysis_label} analysis for the flagged position",
            payload=provenance,
        )
    ]
    mover = before_board.turn
    hanging_piece = _hanging_piece_payload(after_board, mover)
    if hanging_piece is not None and swing_cp >= 200:
        evidence.append(
            _evidence(
                kind="blunder_analysis",
                position_id=position_id,
                engine_analysis_id=engine_analysis_id,
                claim="the played move leaves an attacked piece undefended",
                payload={**provenance, **hanging_piece},
            )
        )

    best_move = _legal_pv_move(before_board, fast_analysis)
    if best_move is not None:
        best_move_uci = best_move.uci()
        if before_board.gives_check(best_move) and swing_cp >= 300:
            evidence.append(
                _evidence(
                    kind="missed_opportunity",
                    position_id=position_id,
                    engine_analysis_id=engine_analysis_id,
                    claim="the fast-pass principal variation starts with a legal check",
                    payload={
                        **provenance,
                        "missed_type": "check",
                        "best_move": best_move_uci,
                        "was_unique_best": (
                            analysis_depth == "deep" and len(fast_analysis.raw_result) == 1
                        ),
                    },
                )
            )
        if before_board.is_capture(best_move) and swing_cp >= 200:
            captured = before_board.piece_at(best_move.to_square)
            evidence.append(
                _evidence(
                    kind="missed_opportunity",
                    position_id=position_id,
                    engine_analysis_id=engine_analysis_id,
                    claim="the fast-pass principal variation starts with a legal capture",
                    payload={
                        **provenance,
                        "missed_type": "capture",
                        "best_move": best_move_uci,
                        "captured_square": square_name(best_move.to_square),
                        "captured_piece": captured.symbol() if captured is not None else None,
                        "was_only_winning_line": (
                            analysis_depth == "deep" and len(fast_analysis.raw_result) == 1
                        ),
                    },
                )
            )
        if swing_cp >= 200:
            for payload in (
                _knight_fork_payload(before_board.copy(), best_move),
                _pin_payload(before_board.copy(), best_move),
                _overload_payload(before_board.copy(), best_move),
            ):
                if payload is not None:
                    evidence.append(
                        _evidence(
                            kind="missed_tactic",
                            position_id=position_id,
                            engine_analysis_id=engine_analysis_id,
                            claim=(
                                "the fast-pass principal variation exposes a tactical opportunity"
                            ),
                            payload={**provenance, **payload, "best_move": best_move_uci},
                        )
                    )

    focused_move = _legal_pv_move(after_board, focused_analysis)
    if focused_move is not None:
        focused_piece = after_board.piece_at(focused_move.to_square)
        after_focused = after_board.copy()
        after_focused.push(focused_move)
        if after_board.is_capture(focused_move) and swing_cp >= 200:
            evidence.append(
                _evidence(
                    kind="blunder_analysis",
                    position_id=position_id,
                    engine_analysis_id=engine_analysis_id,
                    claim=f"the {analysis_label} principal variation immediately captures material",
                    payload={
                        **provenance,
                        "blunder_type": "missed_direct_threat",
                        "threat_move": focused_move.uci(),
                        "threatened_square": square_name(focused_move.to_square),
                        "threatened_piece": focused_piece.symbol()
                        if focused_piece is not None
                        else None,
                        "opponent_executed_threat": True,
                    },
                )
            )
        if after_focused.is_check() and swing_cp >= 100:
            evidence.append(
                _evidence(
                    kind="positional_analysis",
                    position_id=position_id,
                    engine_analysis_id=engine_analysis_id,
                    claim=f"the {analysis_label} principal variation begins with an incoming check",
                    payload={
                        **provenance,
                        "issue": "king_safety_neglect",
                        "incoming_threat": focused_move.uci(),
                    },
                )
            )
    if len(focused_line) >= 3 and swing_cp >= 200:
        evidence.append(
            _evidence(
                kind="calculation_error",
                position_id=position_id,
                engine_analysis_id=engine_analysis_id,
                claim=f"the {analysis_label} principal variation requires a forcing continuation",
                payload={
                    **provenance,
                    "error_type": "stopped_early",
                    "sequence_plies": len(focused_line),
                    "sharp_eval_swing": True,
                },
            )
        )

    opening = _opening_payload(history_san, mover, initial_fen)
    if opening is not None:
        evidence.append(
            _evidence(
                kind="opening_analysis",
                position_id=position_id,
                engine_analysis_id=engine_analysis_id,
                claim="the game history shows delayed minor-piece development",
                payload={**provenance, **opening},
            )
        )
    return evidence
