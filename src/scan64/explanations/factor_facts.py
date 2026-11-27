"""Factor changes across a mistake, as narrator facts (issue #408).

The factor module (``explanations/factors``, #407) evaluates a position as
named parts. This module measures those parts at three points of a mistake and
keeps a change only when the engine's verdict agrees with it:

1. ``before``: the position before the learner's move;
2. ``played``: after the learner's move and the engine's reply line;
3. ``best``: after the engine's best move and its line.

Each line is followed to its first quiet position (``quiet_ply``) within
``FACTOR_CONFIG.quiet_ply_limit`` plies. A change from a reference point
(``before`` or ``best``) to ``played`` becomes a fact only when

- its size reaches the threshold of its kind (``FACTOR_CONFIG``), and
- its sign agrees with the engine's score swing between the two points: the
  engine score at the measured position minus the score at the reference
  position, from the per-move evaluations along the lines (#367). When either
  score is unknown, the swing across the mistake (or between the board lines'
  scores) is used instead.

The engine stays the judge; the factors only name parts of its verdict. Values
are in pawns from the learner's point of view (positive favours the learner).
Analysis Board lines use the same rule with line 1 as the reference and the
score difference between the lines as the swing (``board_factor_changes``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from scan64.explanations.factors import (
    GUIDE_COMMIT,
    PARTS,
    PAWN_VALUE_EG,
    FactorReport,
    evaluate_factors,
)
from scan64.learning.evidence.composer import PIECE_VALUES

# The factor module version every factor fact and slot is labelled with.
FACTOR_SOURCE = f"scan64-factors/1, Stockfish Evaluation Guide {GUIDE_COMMIT[:7]}"


@dataclass(frozen=True)
class FactorConfig:
    """Every tunable of the factor facts, in one table (calibrated in #410).

    ``thresholds`` are the smallest change, in pawns, that becomes a fact:
    ``group`` for an evaluation group (King, Mobility, ...), ``term`` for one
    named part of a group. ``overrides`` replaces that value for one group or
    part, by its guide name. ``excluded_groups`` are never stated: material is
    stated by the capture and material slots; Imbalance and Winnable are
    scaling terms with no plain-word cause. ``max_changes`` caps the facts per
    comparison, largest first, so the prompt stays small; ``max_board_changes``
    caps them per Analysis Board line (up to four lines share one prompt).
    """

    thresholds: Mapping[Literal["group", "term"], float]
    quiet_ply_limit: int
    excluded_groups: frozenset[str]
    max_changes: int
    max_board_changes: int
    # Per-name thresholds (guide group or part name) that replace the kind's
    # value in ``thresholds``; fitted in #410.
    overrides: Mapping[str, float] = field(default_factory=dict)

    def threshold(self, kind: Literal["group", "term"], guide_name: str) -> float:
        """The smallest change, in pawns, of ``guide_name`` that becomes a fact."""
        return self.overrides.get(guide_name, self.thresholds[kind])


# Thresholds fitted in #410 (docs/evaluation/factor-calibration.md): the
# smallest multiple of 0.05 pawns, at least 0.10, that at most 10% of the
# changes at null moves (engine swing within 25 cp) reach. Refit and check with
# ``python -m benchmarks.factor_calibration.calibrate --check``.
FACTOR_CONFIG = FactorConfig(
    thresholds={"group": 0.10, "term": 0.10},
    quiet_ply_limit=8,
    excluded_groups=frozenset({"Material", "Imbalance", "Winnable"}),
    max_changes=3,
    max_board_changes=2,
    overrides={
        # Groups.
        "King": 0.75,
        "Mobility": 0.35,
        "Pawns": 0.25,
        "Pieces": 0.35,
        "Threats": 0.60,
        # Terms whose null changes exceed the term default.
        "Bishop pawns": 0.15,
        "Connected bonus": 0.15,
        "Flank attack": 0.20,
        "Hanging": 0.35,
        "King danger": 0.45,
        "King protector": 0.15,
        "Minor threat": 0.30,
        "Passed file": 0.15,
        "Pawn push threat": 0.25,
        "Restricted": 0.15,
        "Rook on file": 0.15,
        "Rook threat": 0.15,
        "Slider on queen": 0.30,
        "Trapped rook": 0.20,
    },
)

# Plain names, learner's view. A name is also the vocabulary the narrator may
# use outside slots when the change passes (validator, ``chess_token_outside_slot``).
GROUP_NAMES: Mapping[str, str] = {
    "Pawns": "pawn structure",
    "Pieces": "piece placement",
    "Mobility": "mobility",
    "Threats": "threats",
    "Passed pawns": "passed pawns",
    "Space": "space",
    "King": "king safety",
}
TERM_NAMES: Mapping[str, str] = {
    "Doubled isolated": "doubled isolated pawns",
    "Isolated": "isolated pawns",
    "Backward": "backward pawns",
    "Doubled": "doubled pawns",
    "Connected bonus": "connected pawns",
    "Weak unopposed pawn": "weak unopposed pawns",
    "Weak lever": "weak levers",
    "Blocked": "blocked pawns",
    "Outpost total": "outposts",
    "Minor behind pawn": "minor pieces behind pawns",
    "Bishop pawns": "pawns on the bishop's colour",
    "Bishop xray pawns": "bishop x-ray on pawns",
    "Rook on queen file": "rook on the queen's file",
    "Rook on king ring": "rook aimed at the king",
    "Bishop on king ring": "bishop aimed at the king",
    "Rook on file": "rook on an open file",
    "Trapped rook": "trapped rook",
    "Weak queen": "weak queen",
    "Queen infiltration": "queen infiltration",
    "King protector": "king protectors",
    "Long diagonal bishop": "long diagonal bishop",
    "Hanging": "hanging pieces",
    "King threat": "king threats",
    "Pawn push threat": "pawn push threats",
    "Threat safe pawn": "safe pawn threats",
    "Slider on queen": "slider on the queen",
    "Knight on queen": "knight on the queen",
    "Restricted": "restricted pieces",
    "Weak queen protection": "weak queen protection",
    "Minor threat": "minor piece threats",
    "Rook threat": "rook threats",
    "Passed rank": "passed pawn rank",
    "Passed block": "passed pawn blockade",
    "Passed file": "passed pawn file",
    "King proximity": "king proximity to passed pawns",
    "Shelter strength": "pawn shelter",
    "Shelter storm": "pawn storm",
    "King danger": "king attack",
    "Flank attack": "flank attack",
    "Pawnless flank": "pawnless flank",
    "King pawn distance": "king distance to pawns",
    "Endgame shelter": "endgame shelter",
}
# A term that is the only part of its group (Mobility, Space) is the group.
_SINGLE_PART_GROUPS = frozenset(
    group for group in GROUP_NAMES if sum(1 for _, g in PARTS if g == group) == 1
)

FactorKind = Literal["group", "term"]
PointName = Literal["before", "played", "best", "line"]
Comparison = Literal["before", "best"]


class _Facts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactorPoint(_Facts):
    """One measured position, reached by ``uci`` from the position before the mistake.

    Analysis Board: ``best`` is line 1 and ``line`` the explained line, both from
    the board position.
    """

    point: PointName
    uci: tuple[str, ...] = Field(
        default=(), description="Moves from the position before the mistake to this position."
    )
    quiet: bool = Field(
        default=True, description="False: no quiet position within the ply limit (not measured)."
    )


class FactorChange(_Facts):
    """A factor change that passed its threshold and agrees with the engine."""

    key: str = Field(description="Slot key, for example king_safety.")
    name: str = Field(description="Plain name, for example king safety.")
    kind: Literal["group", "term"]
    guide_name: str = Field(description="Group or part name in the Stockfish Evaluation Guide.")
    compared_with: Comparison = Field(
        description="before: the position before the mistake; best: after the best move's "
        "line (Analysis Board: after line 1)."
    )
    change_pawns: float = Field(
        description="Played point minus the reference point, learner's view, pawns."
    )


class FactorSwing(_Facts):
    """The engine swing a comparison's sign rule used."""

    compared_with: Comparison
    swing_cp: int = Field(
        description="Engine score at the played (board: line) point minus the score at the "
        "reference point, learner's view; mates count as large scores."
    )
    source: Literal["points", "mistake"] = Field(
        description="points: per-move evaluations at both measured positions; mistake: the "
        "position scores were unknown, so engine_swing_cp."
    )


class FactorFacts(_Facts):
    source: str = Field(description="Factor module version.")
    points: tuple[FactorPoint, ...]
    engine_swing_cp: int | None = Field(
        default=None,
        description="Engine score after the mistake minus before it, learner's view; mates "
        "count as large scores. Absent: no factor fact (the engine gave no direction).",
    )
    swings: tuple[FactorSwing, ...] = Field(
        default=(), description="The swing each comparison used (#367); absent before #367."
    )
    changes: tuple[FactorChange, ...] = ()


# Learner-view engine score, as ``evaluation_cp``, of the position a move
# prefix from the measured root reaches; ``None`` when unscored.
PointScore = Callable[[Sequence[str]], int | None]


def _swing(
    point_score: PointScore | None,
    measured: FactorPoint,
    reference: FactorPoint,
    compared_with: Comparison,
    fallback: int | None,
) -> FactorSwing | None:
    if point_score is not None:
        end, start = point_score(measured.uci), point_score(reference.uci)
        if end is not None and start is not None:
            return FactorSwing(compared_with=compared_with, swing_cp=end - start, source="points")
    if fallback is None:
        return None
    return FactorSwing(compared_with=compared_with, swing_cp=fallback, source="mistake")


def factor_key(name: str) -> str:
    """``king safety`` -> ``king_safety``; slot names hold only [a-z_]."""
    return "".join(c if c.isalpha() else "_" for c in name.lower()).strip("_")


# --- Quiet positions ---------------------------------------------------------------


def _pending_capture(board: chess.Board) -> bool:
    """A capture the side to move should make: an undefended or more valuable target."""
    for move in board.legal_moves:
        if not board.is_capture(move):
            continue
        if board.is_en_passant(move):
            return True
        target = board.piece_at(move.to_square)
        mover = board.piece_at(move.from_square)
        if target is None or mover is None:
            continue
        if not board.is_attacked_by(target.color, move.to_square):
            return True
        if PIECE_VALUES.get(target.piece_type, 0) > PIECE_VALUES.get(mover.piece_type, 0):
            return True
    return False


def quiet_ply(
    board: chess.Board, uci: Sequence[str], start: int, limit: int = FACTOR_CONFIG.quiet_ply_limit
) -> int | None:
    """The first ply ``p`` in ``start..limit`` after which ``uci`` reaches a quiet position.

    Quiet: the side to move is not in check, and the line's next move is not a
    capture or a check. At the end of the line, where the engine names no next
    move, quiet also needs no pending capture (``_pending_capture``).
    ``None``: no quiet position within ``limit`` plies.
    """
    replay = board.copy(stack=False)
    moves = [chess.Move.from_uci(token) for token in uci]
    for ply, move in enumerate(moves[: min(len(moves), limit)], start=1):
        replay.push(move)
        if ply < start or replay.is_check():
            continue
        if ply < len(moves):
            following = moves[ply]
            if not replay.is_capture(following) and not replay.gives_check(following):
                return ply
        elif not _pending_capture(replay):
            return ply
    return None


# --- Measurement -------------------------------------------------------------------


def _learner_values(
    report: FactorReport, learner: chess.Color
) -> dict[tuple[FactorKind, str], float]:
    """``(kind, guide name)`` -> pawns, learner's view."""
    sign = 1 if learner == chess.WHITE else -1
    values: dict[tuple[FactorKind, str], float] = {}
    for name, group in report.groups.items():
        if name in GROUP_NAMES:
            values[("group", name)] = sign * group.tapered / PAWN_VALUE_EG
    for part in report.contributions:
        if part.name in TERM_NAMES and part.group not in _SINGLE_PART_GROUPS:
            values[("term", part.name)] = sign * part.tapered / PAWN_VALUE_EG
    return values


def evaluation_cp(status: str, value: int | None) -> int | None:
    """A score as centipawns for its sign; a mate counts as +-(100000 - distance)."""
    if value is None:
        return None
    if status == "mate":
        if value == 0:
            return None
        return (100_000 - abs(value)) * (1 if value > 0 else -1)
    return value


def passed_changes(
    played: Mapping[tuple[FactorKind, str], float],
    reference: Mapping[tuple[FactorKind, str], float],
    swing_cp: int,
    compared_with: Comparison,
    config: FactorConfig = FACTOR_CONFIG,
) -> list[FactorChange]:
    """Changes ``played - reference`` that pass the threshold and agree with ``swing_cp``."""
    if swing_cp == 0:
        return []
    changes = []
    for (kind, guide_name), value in played.items():
        change = value - reference.get((kind, guide_name), 0.0)
        if abs(change) < config.threshold(kind, guide_name) or (change > 0) != (swing_cp > 0):
            continue
        group = guide_name if kind == "group" else dict(PARTS)[guide_name]
        if group in config.excluded_groups:
            continue
        name = GROUP_NAMES[guide_name] if kind == "group" else TERM_NAMES[guide_name]
        changes.append(
            FactorChange(
                key=factor_key(name),
                name=name,
                kind=kind,
                guide_name=guide_name,
                compared_with=compared_with,
                change_pawns=round(change, 2),
            )
        )
    changes.sort(key=lambda c: (-abs(c.change_pawns), c.kind != "group", c.key))
    return changes[: config.max_changes]


@dataclass(frozen=True)
class MeasuredPoint:
    point: FactorPoint
    values: Mapping[tuple[FactorKind, str], float] | None


def measure_point(
    before: chess.Board,
    point: PointName,
    uci: Sequence[str],
    start: int,
    learner: chess.Color,
    config: FactorConfig = FACTOR_CONFIG,
) -> MeasuredPoint:
    """Factor values at the first quiet position of ``uci`` from ``before`` (ply >= ``start``)."""
    if start == 0 and not uci:
        return MeasuredPoint(
            FactorPoint(point=point), _learner_values(evaluate_factors(before), learner)
        )
    ply = quiet_ply(before, uci, start, config.quiet_ply_limit)
    if ply is None:
        return MeasuredPoint(FactorPoint(point=point, uci=tuple(uci[:start]), quiet=False), None)
    board = before.copy(stack=False)
    for token in uci[:ply]:
        board.push(chess.Move.from_uci(token))
    try:
        report = evaluate_factors(board)
    except ValueError:
        return MeasuredPoint(FactorPoint(point=point, uci=tuple(uci[:ply]), quiet=False), None)
    return MeasuredPoint(
        FactorPoint(point=point, uci=tuple(uci[:ply])), _learner_values(report, learner)
    )


def mistake_factor_facts(
    before: chess.Board,
    played_line: Sequence[str],
    best_line: Sequence[str] | None,
    swing_cp: int | None,
    config: FactorConfig = FACTOR_CONFIG,
    *,
    point_score: PointScore | None = None,
) -> FactorFacts:
    """Factor facts of one mistake; ``played_line`` starts with the learner's move.

    ``point_score`` gives the engine score at a measured position; each
    comparison's sign rule uses the score change between its two points, or
    ``swing_cp`` when either score is unknown.
    """
    learner = before.turn
    measured = [measure_point(before, "before", (), 0, learner, config)]
    measured.append(measure_point(before, "played", played_line, 1, learner, config))
    if best_line:
        measured.append(measure_point(before, "best", best_line, 1, learner, config))
    by_point = {m.point.point: m for m in measured}
    changes: list[FactorChange] = []
    swings: list[FactorSwing] = []
    played = by_point.get("played")
    if played is not None and played.values is not None:
        for reference in ("before", "best"):
            other = by_point.get(reference)
            if other is None or other.values is None:
                continue
            swing = _swing(point_score, played.point, other.point, reference, swing_cp)
            if swing is None:
                continue
            swings.append(swing)
            changes += passed_changes(
                played.values, other.values, swing.swing_cp, reference, config
            )
    return FactorFacts(
        source=FACTOR_SOURCE,
        points=tuple(m.point for m in measured),
        engine_swing_cp=swing_cp,
        swings=tuple(swings),
        changes=tuple(changes),
    )


def line_factor_facts(
    root: chess.Board,
    best_line: Sequence[str],
    line: Sequence[str],
    swing_cp: int | None,
    config: FactorConfig = FACTOR_CONFIG,
    *,
    point_score: PointScore | None = None,
) -> FactorFacts:
    """Analysis Board: ``line`` against line 1 (``best_line``), side to move's view.

    ``swing_cp`` is this line's score minus line 1's score; the sign rule uses
    the score change between the two measured positions when ``point_score``
    knows both.
    """
    side = root.turn
    best = measure_point(root, "best", best_line, 1, side, config)
    this = measure_point(root, "line", line, 1, side, config)
    changes: list[FactorChange] = []
    swings: list[FactorSwing] = []
    if best.values is not None and this.values is not None:
        swing = _swing(point_score, this.point, best.point, "best", swing_cp)
        if swing is not None:
            swings.append(swing)
            changes = passed_changes(this.values, best.values, swing.swing_cp, "best", config)
            changes = changes[: config.max_board_changes]
    return FactorFacts(
        source=FACTOR_SOURCE,
        points=(best.point, this.point),
        engine_swing_cp=swing_cp,
        swings=tuple(swings),
        changes=tuple(changes),
    )


__all__ = [
    "FactorSwing",
    "PointScore",
    "line_factor_facts",
    "FACTOR_CONFIG",
    "FACTOR_SOURCE",
    "GROUP_NAMES",
    "TERM_NAMES",
    "FactorChange",
    "FactorConfig",
    "FactorFacts",
    "FactorPoint",
    "evaluation_cp",
    "factor_key",
    "measure_point",
    "mistake_factor_facts",
    "passed_changes",
    "quiet_ply",
]
