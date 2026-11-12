"""Conservative, line-backed position hints; no generated chess claims."""

from typing import Literal

import chess
from chess_lesson_spec.models import (
    DrawArrowCommand,
    HighlightSquareCommand,
    VisualizationCommand,
)
from pydantic import BaseModel, Field, SerializeAsAny

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.exercises.sound_moves import sound_moves

Motif = Literal[
    "forced_mate",
    "mate_threat",
    "check",
    "free_capture",
    "fork",
    "rescue",
    "defence",
    "development",
    "castling",
]
VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}


class HintReason(BaseModel):
    kind: Motif
    description: str
    line: list[str] = Field(min_length=1)
    squares: list[str]


class HintCandidate(BaseModel):
    number: int
    move: str
    san: str
    line: list[str]
    reasons: list[HintReason]
    fallback: bool
    visualizations: list[SerializeAsAny[VisualizationCommand]]


class PositionInsight(BaseModel):
    step: Literal[3] = 3
    fen: str
    candidates: list[HintCandidate]
    visualizations: list[SerializeAsAny[VisualizationCommand]]


class NudgeInsight(BaseModel):
    step: Literal[1] = 1
    text: str
    visualizations: list[SerializeAsAny[VisualizationCommand]]


class IdeaInsight(BaseModel):
    step: Literal[2] = 2
    text: str
    motif: str
    piece: str
    square: str
    targets: list[str]
    visualizations: list[SerializeAsAny[VisualizationCommand]]


InsightStep = NudgeInsight | IdeaInsight | PositionInsight


def insight_step(board: chess.Board, insight: PositionInsight, step: int) -> InsightStep:
    """Project only the requested step; never copy PV descriptions into early hints.

    Even a motif's targets can give away a capture or a pawn's SAN. Exclude every
    candidate destination, not just the first candidate's, from both early steps.
    FEN is deliberately absent: rank encodings can themselves contain pawn SAN.
    """
    if step == 3:
        return insight
    if step not in (1, 2) or not insight.candidates:
        raise ValueError("No hint available for this step")
    candidate = insight.candidates[0]
    move = chess.Move.from_uci(candidate.move)
    destinations = {item.move[2:4] for item in insight.candidates}
    origin = chess.square_name(move.from_square)
    piece_type = board.piece_type_at(move.from_square)
    assert piece_type is not None
    piece = chess.piece_name(piece_type)
    reason = next(iter(candidate.reasons), None)
    targets = list(
        dict.fromkeys(
            square for square in (reason.squares if reason else []) if square not in destinations
        )
    )
    if step == 1:
        square = next(iter(targets), origin)
        return NudgeInsight(
            text="What can you notice around the highlighted square?",
            visualizations=[
                HighlightSquareCommand(square=square, description="Inspect this square.")
            ],
        )
    motif = reason.kind.replace("_", " ") if reason else "engine preference"
    text = f"Look for {motif} with your {piece}."
    return IdeaInsight(
        text=text,
        motif=motif,
        piece=piece,
        square=origin,
        targets=targets,
        visualizations=[
            HighlightSquareCommand(square=origin, description=f"Consider your {piece}."),
            *(
                HighlightSquareCommand(square=square, description="Idea target.")
                for square in targets
                if square != origin
            ),
        ],
    )


def _capturers(board: chess.Board, square: chess.Square, color: chess.Color) -> list[chess.Move]:
    """Legal captures, not geometric attacks by pinned pieces."""
    probe = board.copy(stack=False)
    probe.turn = color
    probe.ep_square = None
    return [
        move for move in probe.legal_moves if move.to_square == square and probe.is_capture(move)
    ]


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(
        VALUES[p.piece_type] * (1 if p.color == color else -1) for p in board.piece_map().values()
    )


def extract_reasons(
    board: chess.Board, line: list[str], *, winning_mate: bool = False
) -> list[HintReason]:
    """Each witness is an exact legal PV prefix, never a competing variation.

    A mate *threat* is a static predicate after the candidate: the mover would
    have mate in one if allowed another turn. It does not claim that the actual
    engine reply permits mate (usually the reply prevents it).
    """
    if not line:
        raise ValueError("Empty engine line")
    replay = board.copy(stack=False)
    positions = []
    for uci in line:
        move = chess.Move.from_uci(uci)
        if move not in replay.legal_moves:
            raise ValueError("Illegal engine line")
        replay.push(move)
        positions.append(replay.copy(stack=False))
    move = chess.Move.from_uci(line[0])
    after = positions[0]
    color = board.turn
    piece = board.piece_at(move.from_square)
    assert piece is not None
    moved_piece = after.piece_at(move.to_square)
    assert moved_piece is not None
    reasons: list[HintReason] = []

    def add(kind: Motif, description: str, squares: list[chess.Square], length: int = 1) -> None:
        reasons.append(
            HintReason(
                kind=kind,
                description=description,
                line=line[:length],
                squares=[chess.square_name(s) for s in squares],
            )
        )

    # A mate score alone is insufficient: the supplied PV must finish in mate.
    if winning_mate and replay.is_checkmate() and replay.turn != color:
        king = replay.king(replay.turn)
        assert king is not None
        add("forced_mate", "The engine line ends in checkmate.", [king], len(line))

    if not after.is_check():
        threat = after.copy(stack=False)
        threat.turn = color
        threat.ep_square = None
        existing_mates = set()
        before = board.copy(stack=False)
        before.ep_square = None
        for existing_move in list(before.legal_moves):
            before.push(existing_move)
            if before.is_checkmate():
                existing_mates.add(existing_move)
            before.pop()
        for mating_move in list(threat.legal_moves):
            san = threat.san(mating_move)
            threat.push(mating_move)
            mates = threat.is_checkmate()
            threat.pop()
            if mates and mating_move not in existing_mates:
                add(
                    "mate_threat",
                    f"Threatens {san} if the opponent does not respond.",
                    [mating_move.to_square],
                )
                break

    targets = [
        square
        for square in after.attacks(move.to_square)
        if (target := after.piece_at(square)) is not None
        and target.color != color
        and VALUES[target.piece_type] > VALUES[moved_piece.piece_type]
        and (
            target.piece_type == chess.KING
            or any(
                capture.from_square == move.to_square
                for capture in _capturers(after, square, color)
            )
        )
    ]
    # A fork means simultaneous attacks, not a promise that both pieces fall.
    if len(targets) >= 2:
        names = []
        for square in targets:
            target_type = after.piece_type_at(square)
            assert target_type is not None
            names.append(chess.piece_name(target_type))
        add("fork", "Forks " + " and ".join(names) + ".", targets)
    if after.is_check():
        king = after.king(not color)
        assert king is not None
        add("check", "Gives check.", [king])

    captured = board.piece_at(move.to_square)
    if (
        captured is not None
        and board.is_capture(move)
        and not _capturers(after, move.to_square, not color)
    ):
        gain = VALUES[captured.piece_type]
        baseline = _material(board, color)
        if all(_material(p, color) - baseline >= gain for p in positions):
            add(
                "free_capture",
                f"Captures an undefended {chess.piece_name(captured.piece_type)}; "
                "the line keeps the material gain.",
                [move.to_square],
                len(line),
            )

    if (
        piece.piece_type != chess.KING
        and _capturers(board, move.from_square, not color)
        and not _capturers(after, move.to_square, not color)
    ):
        add(
            "rescue",
            f"Moves the attacked {chess.piece_name(piece.piece_type)} out of capture.",
            [move.from_square, move.to_square],
        )
    for square, target in board.piece_map().items():
        if target.color != color or square == move.from_square or target.piece_type == chess.KING:
            continue
        if not _capturers(board, square, not color):
            continue
        # Demonstrate a legal recapture by the moved piece after an actual capture.
        for capture in _capturers(after, square, not color):
            probe = after.copy(stack=False)
            probe.push(capture)
            recapture = chess.Move(move.to_square, square)
            if recapture in probe.legal_moves:
                add(
                    "defence",
                    f"Defends the attacked {chess.piece_name(target.piece_type)} "
                    f"on {chess.square_name(square)}.",
                    [square],
                )
                break
    if board.is_castling(move):
        add("castling", "Castles the king and rook.", [move.to_square])
    elif (
        piece.piece_type in (chess.KNIGHT, chess.BISHOP)
        and chess.square_rank(move.from_square) == (0 if color else 7)
        and chess.square_rank(move.to_square) != chess.square_rank(move.from_square)
    ):
        add(
            "development",
            f"Develops a {chess.piece_name(piece.piece_type)} from the back rank.",
            [move.from_square, move.to_square],
        )
    return reasons


def position_insight(board: chess.Board, analysis: EngineAnalysis) -> PositionInsight:
    accepted = {move.uci() for move in sound_moves(board, analysis)}
    candidates: list[HintCandidate] = []
    seen: set[str] = set()
    for raw in analysis.raw_result:
        line = raw.get("pv")
        if not isinstance(line, list) or not line or not all(isinstance(uci, str) for uci in line):
            raise ValueError("Invalid engine line")
        if line[0] not in accepted or line[0] in seen:
            continue
        seen.add(line[0])
        number = len(candidates) + 1
        reasons = extract_reasons(board, line, winning_mate=raw.get("score_mate", 0) > 0)
        move = chess.Move.from_uci(line[0])
        san = board.san(move)
        drawings: list[VisualizationCommand] = [
            DrawArrowCommand(
                origin=chess.square_name(move.from_square),
                target=chess.square_name(move.to_square),
                description=f"{number}. {san}",
            )
        ]
        for reason in reasons:
            drawings.extend(
                HighlightSquareCommand(square=square, description=reason.description)
                for square in reason.squares
            )
        candidates.append(
            HintCandidate(
                number=number,
                move=line[0],
                san=san,
                line=line,
                reasons=reasons,
                fallback=not reasons,
                visualizations=drawings,
            )
        )
        if len(candidates) == 3:
            break
    return PositionInsight(
        fen=board.fen(),
        candidates=candidates,
        visualizations=[
            drawing for candidate in candidates for drawing in candidate.visualizations
        ],
    )
