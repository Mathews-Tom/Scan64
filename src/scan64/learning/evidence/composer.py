from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from uuid import uuid4

from chess import (
    BISHOP,
    KING,
    KNIGHT,
    PAWN,
    QUEEN,
    ROOK,
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

DIAGNOSIS_RULE_VERSION = "m48-fast-local-v1"


def _capture_square(board: Board, move: Move) -> int:
    if board.is_en_passant(move):
        return move.to_square - 8 if board.turn else move.to_square + 8
    return move.to_square


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


def mating_line_san(board: Board, line: object, mate_in: object) -> list[str] | None:
    """Validate the engine's complete mating PV, not a claim from an attack map."""
    if (
        not isinstance(mate_in, int)
        or isinstance(mate_in, bool)
        or not 1 <= mate_in <= 3
        or not isinstance(line, list)
        or len(line) != 2 * mate_in - 1
    ):
        return None
    replay = board.copy(stack=False)
    san = []
    for uci in line:
        if not isinstance(uci, str):
            return None
        try:
            move = Move.from_uci(uci)
        except ValueError:
            return None
        if move not in replay.legal_moves:
            return None
        san.append(replay.san(move))
        replay.push(move)
    return san if replay.is_checkmate() else None


def needs_mate_source(analysis: EngineAnalysis) -> bool:
    """Only mate-range replies need the additional million-node source search."""
    mate = analysis.raw_result[0].get("score_mate") if analysis.raw_result else None
    return isinstance(mate, int) and not isinstance(mate, bool) and 1 <= mate <= 3


def allowed_mate_line(payload: Mapping[str, Any]) -> list[str] | None:
    """Revalidate the scored source-depth evidence and the complete mating PV."""
    before_mate = payload.get("before_score_mate")
    source_config = payload.get("mate_source_config")
    if (
        payload.get("blunder_type") != "allowed_mate"
        or not isinstance(source_config, dict)
        or source_config.get("nodes") != 1_000_000
        or not isinstance(payload.get("mate_source_analysis_id"), str)
        or not (
            isinstance(before_mate, int)
            and before_mate > 0
            or before_mate is None
            and isinstance(payload.get("before_score_cp"), int)
        )
        or payload.get("after_score_mate") != payload.get("mate_in")
    ):
        return None
    fen = payload.get("after_fen")
    if not isinstance(fen, str):
        return None
    try:
        board = Board(fen)
    except ValueError:
        return None
    return mating_line_san(board, payload.get("mating_line"), payload.get("mate_in"))


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


def line_material_gain(board: Board, line: list[str]) -> int | None:
    """Account for every legal capture, exchange and promotion in a complete PV.

    The result is from the initial side-to-move's perspective. Missing or
    illegal lines prove nothing; attack maps alone never prove a material win.
    """
    if not line:
        return None
    replay = board.copy()
    attacker = board.turn
    gain = 0
    for uci in line:
        try:
            move = Move.from_uci(uci)
        except ValueError:
            return None
        if move not in replay.legal_moves:
            return None
        sign = 1 if replay.turn == attacker else -1
        if replay.is_en_passant(move):
            gain += sign
        elif (victim := replay.piece_at(move.to_square)) is not None:
            gain += sign * _piece_value(victim)
        if move.promotion is not None:
            gain += sign * (_PIECE_VALUES[move.promotion] - 1)
        replay.push(move)
    return gain


def exchange_material_gain(board: Board, line: list[str]) -> int | None:
    """Price only consecutive captures on the initial capture's target square."""
    replay = board.copy()
    captures: list[str] = []
    target: int | None = None
    for uci in line:
        try:
            move = Move.from_uci(uci)
        except ValueError:
            return None
        if move not in replay.legal_moves:
            return None
        if not replay.is_capture(move) or (target is not None and move.to_square != target):
            break
        target = move.to_square
        captures.append(uci)
        replay.push(move)
    return line_material_gain(board, captures)


def hanging_piece_proof(board: Board, mover: Color, line: list[str]) -> dict[str, Any] | None:
    """Name only a piece won in its own exchange, not through a later capture."""
    gain = line_material_gain(board, line)
    local_gain = exchange_material_gain(board, line)
    if gain is None or gain < 1 or local_gain is None or local_gain < 1 or board.turn == mover:
        return None
    move = Move.from_uci(line[0])
    victim = board.piece_at(move.to_square)
    if (
        not board.is_capture(move)
        or victim is None
        or victim.color != mover
        or victim.piece_type in (PAWN, KING)
    ):
        return None
    return {
        "blunder_type": "hanging_piece_lost",
        "hanging_square": square_name(move.to_square),
        "hanging_piece": victim.symbol(),
        "material_gain": gain,
        "exchange_gain": local_gain,
    }


def _knight_fork_payload(board: Board, move: Move, gain: int | None) -> dict[str, Any] | None:
    moving_piece = board.piece_at(move.from_square)
    if moving_piece is None or moving_piece.piece_type != KNIGHT or gain is None or gain < 1:
        return None
    board.push(move)
    targets = []
    for square in board.attacks(move.to_square):
        target = board.piece_at(square)
        if target is None or target.color == moving_piece.color:
            continue
        if target.piece_type == KING or _piece_value(target) >= 3:
            targets.append({"square": square_name(square), "piece": target.symbol()})
    if len(targets) < 2:
        return None
    return {
        "tactic_type": "knight_fork",
        "fork_square": square_name(move.to_square),
        "targets": targets,
        "results_in_material_gain": gain >= 1,
    }


def _pin_payload(board: Board, move: Move, gain: int | None) -> dict[str, Any] | None:
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
                "wins_material": gain is not None and gain >= 1,
                "pinning_color": "white" if moving_color else "black",
            }
    return None


def _overload_payload(board: Board, move: Move, gain: int | None) -> dict[str, Any] | None:
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
                "wins_material": gain is not None and gain >= 1,
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
    reply_analysis: EngineAnalysis,
    focused_analysis: EngineAnalysis,
    mate_source_analysis: EngineAnalysis | None,
    played_move: str,
    swing_cp: int,
    analysis_depth: Literal["deep", "interactive"] = "deep",
) -> list[Evidence]:
    """Use fast local signals; confirm newly allowed mates with a deep source search."""
    analysis_label = "deep MultiPV" if analysis_depth == "deep" else "bounded interactive"
    focused_line = _pv_line(focused_analysis)
    reply_line = _pv_line(reply_analysis)
    reply_gain = line_material_gain(after_board, reply_line)
    reply_exchange_gain = exchange_material_gain(after_board, reply_line)
    fast_line = _pv_line(fast_analysis)
    fast_gain = line_material_gain(before_board, fast_line)
    fast_exchange_gain = exchange_material_gain(before_board, fast_line)
    provenance: dict[str, Any] = {
        "played_move": played_move,
        "fast_analysis_id": str(fast_analysis.id),
        "reply_analysis_id": str(reply_analysis.id),
        "fast_line": fast_line,
        "proving_line": reply_line,
        "diagnosis_rule_version": DIAGNOSIS_RULE_VERSION,
        "focused_analysis_id": str(focused_analysis.id),
        "focused_multipv": focused_analysis.raw_result,
        "focused_line": focused_line,
        "after_fen": after_board.fen(),
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
    before_score = (
        mate_source_analysis.raw_result[0]
        if mate_source_analysis is not None and mate_source_analysis.raw_result
        else {}
    )
    after_score = reply_analysis.raw_result[0] if reply_analysis.raw_result else {}
    before_mate = before_score.get("score_mate")
    after_mate = after_score.get("score_mate")
    before_not_mated = (
        isinstance(before_mate, int)
        and before_mate > 0
        or before_mate is None
        and isinstance(before_score.get("score_cp"), int)
    )
    if (
        mate_source_analysis is not None
        and mate_source_analysis.config.get("nodes") == 1_000_000
        and before_not_mated
        and mating_line_san(after_board, reply_line, after_mate) is not None
    ):
        evidence.append(
            _evidence(
                kind="blunder_analysis",
                position_id=position_id,
                engine_analysis_id=engine_analysis_id,
                claim="the move newly allows the opponent's engine-proved mate in at most three",
                payload={
                    **provenance,
                    "blunder_type": "allowed_mate",
                    "mate_rule_version": "m78-focused-source-v1",
                    "mate_source_analysis_id": str(mate_source_analysis.id),
                    "mate_source_config": mate_source_analysis.config,
                    "mate_source_multipv": mate_source_analysis.raw_result,
                    "before_score_mate": before_mate,
                    "before_score_cp": before_score.get("score_cp"),
                    "after_score_mate": after_mate,
                    "mate_in": after_mate,
                    "mating_line": reply_line,
                },
            )
        )
    hanging_piece = hanging_piece_proof(after_board, mover, reply_line)
    if hanging_piece is not None and swing_cp >= 200:
        evidence.append(
            _evidence(
                kind="blunder_analysis",
                position_id=position_id,
                engine_analysis_id=engine_analysis_id,
                claim="the fast-pass reply wins this piece in its own local exchange",
                payload={**provenance, **hanging_piece},
            )
        )

    best_move = _legal_pv_move(before_board, fast_analysis)
    if best_move is not None:
        best_move_uci = best_move.uci()
        best_score = fast_analysis.raw_result[0]
        winning_check = best_score.get("score_cp", 0) >= 300 or best_score.get("score_mate", 0) > 0
        if before_board.gives_check(best_move) and swing_cp >= 300 and winning_check:
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
                    },
                )
            )
        if (
            before_board.is_capture(best_move)
            # A shallow score swing can flag even the move the fast PV recommends.
            # Taking that very piece is not a missed capture.
            and swing_cp >= 200
            and best_move != before_board.parse_san(played_move)
            and fast_gain is not None
            and fast_gain >= 1
            and fast_exchange_gain is not None
            and fast_exchange_gain >= 1
        ):
            captured_square = _capture_square(before_board, best_move)
            captured = before_board.piece_at(captured_square)
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
                        "captured_square": square_name(captured_square),
                        "captured_piece": captured.symbol() if captured is not None else None,
                        "exchange_gain": fast_exchange_gain,
                        "material_gain": fast_gain,
                    },
                )
            )
        if swing_cp >= 200:
            for payload in (
                _knight_fork_payload(before_board.copy(), best_move, fast_gain),
                _pin_payload(before_board.copy(), best_move, fast_gain),
                _overload_payload(before_board.copy(), best_move, fast_gain),
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

    reply_move = _legal_pv_move(after_board, reply_analysis)
    if reply_move is not None:
        reply_square = _capture_square(after_board, reply_move)
        reply_piece = after_board.piece_at(reply_square)
        after_reply = after_board.copy()
        after_reply.push(reply_move)
        if (
            after_board.is_capture(reply_move)
            and swing_cp >= 200
            and reply_gain is not None
            and reply_gain >= 1
            and reply_exchange_gain is not None
            and reply_exchange_gain >= 1
        ):
            evidence.append(
                _evidence(
                    kind="blunder_analysis",
                    position_id=position_id,
                    engine_analysis_id=engine_analysis_id,
                    claim="the fast-pass reply begins with a profitable local capture",
                    payload={
                        **provenance,
                        "blunder_type": "missed_direct_threat",
                        "threat_move": reply_move.uci(),
                        "threatened_square": square_name(reply_square),
                        "threatened_piece": reply_piece.symbol()
                        if reply_piece is not None
                        else None,
                        "opponent_executed_threat": reply_gain >= 1,
                        "material_gain": reply_gain,
                        "exchange_gain": reply_exchange_gain,
                    },
                )
            )
        if after_reply.is_check() and swing_cp >= 100:
            evidence.append(
                _evidence(
                    kind="positional_analysis",
                    position_id=position_id,
                    engine_analysis_id=engine_analysis_id,
                    claim="the fast-pass reply begins with an incoming check",
                    payload={
                        **provenance,
                        "issue": "king_safety_neglect",
                        "incoming_threat": reply_move.uci(),
                    },
                )
            )
    # A multi-ply line and position-level swing are only a hypothesis about
    # calculation, never evidence of where the human stopped thinking.
    if len(reply_line) >= 3 and swing_cp >= 200:
        evidence.append(
            _evidence(
                kind="calculation_error",
                position_id=position_id,
                engine_analysis_id=engine_analysis_id,
                claim="unvalidated calculation hypothesis from a multi-ply fast reply and swing",
                payload={
                    **provenance,
                    "error_type": "stopped_early",
                    "sequence_plies": len(reply_line),
                    "sharp_eval_swing": swing_cp >= 200,
                    "hypothesis_only": True,
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
