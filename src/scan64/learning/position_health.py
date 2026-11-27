"""Position health: factor groups (#407) as meters from healthy to in trouble.

Each side gets one value per factor group, in pawns, tapered by game phase.
A value maps to a 0-1 health with a logistic around that side's value in the
starting position: ``health = 1 / (1 + exp(-(OFFSET + (value - start) / scale)))``.
At the start every meter reads ``logistic(OFFSET)`` (about 0.88, healthy); a
loss of ``OFFSET`` scales against the start reads 0.5.

The scales are PROVISIONAL. #410 fitted change thresholds (when a change across
a move becomes a fact), not a scale for a value at one position; linux-5090 is
fitting that scale (#416). Until it lands, each group's scale is its #410
change threshold with a floor of ``SCALE_FLOOR`` pawns. ``HEALTH_SCALE`` is the
one table to refit.

Side values: most parts of the guide belong to the side they describe. The
attacking parts belong to the attacker (Black's ``King danger`` is Black's
attack on White's king; Black's ``Threats`` are threats against White), so a
side's king safety subtracts the opponent's attack parts and a side's threats
meter reads the threats it faces.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import chess

from scan64.explanations.factors import evaluate_factors
from scan64.explanations.factors.report import Contribution, FactorReport

Side = Literal["white", "black"]
Level = Literal["good", "fair", "low"]
Trend = Literal["rising", "falling", "steady"]

OFFSET = 2.0
SCALE_FLOOR = 0.25
# Health at or above ``good`` reads good, below ``fair`` reads low.
LEVELS: Mapping[Level, float] = {"good": 2 / 3, "fair": 1 / 3}
# A health change smaller than this reads steady.
TREND_STEP = 0.05
# Terms shown in a meter's details view, largest first.
DETAIL_TERMS = 3


@dataclass(frozen=True)
class GroupHealth:
    """One meter: its guide group, plain name and provisional scale (pawns)."""

    key: str
    name: str
    scale: float
    # Parts credited to the opponent that count against this side.
    opponent_parts: frozenset[str] = frozenset()
    # True: the meter reads the opponent's group value against this side.
    opponent_group: bool = False


# Order is the panel order. Scales: #410 change thresholds, floor SCALE_FLOOR.
HEALTH_SCALE: tuple[GroupHealth, ...] = (
    GroupHealth("King", "king safety", 0.75, frozenset({"King danger", "Flank attack"})),
    GroupHealth("Material", "material", 1.0),
    GroupHealth("Threats", "threats", 0.60, opponent_group=True),
    GroupHealth("Pieces", "pieces", 0.35),
    GroupHealth("Mobility", "mobility", 0.35),
    GroupHealth("Pawns", "pawn structure", max(0.25, SCALE_FLOOR)),
    GroupHealth("Passed pawns", "passed pawns", max(0.10, SCALE_FLOOR)),
    GroupHealth("Space", "space", max(0.10, SCALE_FLOOR)),
)

START_FEN = chess.STARTING_FEN


@dataclass(frozen=True)
class TermReading:
    name: str
    pawns: float


@dataclass(frozen=True)
class MeterReading:
    health: float
    level: Level
    trend: Trend
    terms: tuple[TermReading, ...]


@dataclass(frozen=True)
class GroupReading:
    key: str
    name: str
    learner: MeterReading
    opponent: MeterReading


def _tapered(mg: int | None, eg: int | None, phase: int) -> float:
    if mg is None or eg is None:
        return 0.0
    return (mg * phase + eg * (128 - phase)) / 128


def _part_pawns(part: Contribution, side: Side, phase: int) -> float:
    mg, eg = (part.white_mg, part.white_eg) if side == "white" else (part.black_mg, part.black_eg)
    return FactorReport.to_pawns(_tapered(mg, eg, phase))


def _other(side: Side) -> Side:
    return "black" if side == "white" else "white"


def _parts(report: FactorReport, group: GroupHealth, side: Side) -> list[TermReading]:
    """Signed parts of ``side``'s meter, in pawns: positive helps ``side``."""
    readings = []
    for part in report.contributions:
        if part.group != group.key:
            continue
        if group.opponent_group:
            readings.append(TermReading(part.name, -_part_pawns(part, _other(side), report.phase)))
        elif part.name in group.opponent_parts:
            readings.append(TermReading(part.name, -_part_pawns(part, _other(side), report.phase)))
        else:
            readings.append(TermReading(part.name, _part_pawns(part, side, report.phase)))
    return readings


def _value(report: FactorReport, group: GroupHealth, side: Side) -> float:
    return sum(reading.pawns for reading in _parts(report, group, side))


_START = evaluate_factors(START_FEN)


def health(value: float, start: float, scale: float) -> float:
    return 1 / (1 + math.exp(-(OFFSET + (value - start) / scale)))


def level(value: float) -> Level:
    if value >= LEVELS["good"]:
        return "good"
    return "fair" if value >= LEVELS["fair"] else "low"


def trend(now: float, before: float | None) -> Trend:
    if before is None or abs(now - before) < TREND_STEP:
        return "steady"
    return "rising" if now > before else "falling"


def _meter(
    report: FactorReport, previous: FactorReport | None, group: GroupHealth, side: Side
) -> MeterReading:
    start = _value(_START, group, side)
    now = health(_value(report, group, side), start, group.scale)
    before = None if previous is None else health(_value(previous, group, side), start, group.scale)
    parts = sorted(_parts(report, group, side), key=lambda r: abs(r.pawns), reverse=True)
    terms = tuple(
        TermReading(r.name, round(r.pawns, 2)) for r in parts[:DETAIL_TERMS] if r.pawns != 0
    )
    return MeterReading(round(now, 3), level(now), trend(now, before), terms)


def position_health(
    board: chess.Board, learner: Side, previous: chess.Board | None = None
) -> tuple[GroupReading, ...]:
    """Every meter for ``learner`` and the opponent; ``previous`` gives the trend."""
    report = evaluate_factors(board)
    before = None if previous is None else evaluate_factors(previous)
    return tuple(
        GroupReading(
            group.key,
            group.name,
            _meter(report, before, group, learner),
            _meter(report, before, group, _other(learner)),
        )
        for group in HEALTH_SCALE
    )
