"""Position health: factor groups (#407) as meters from healthy to in trouble.

The values and their scale are #410's (``scan64.explanations.factor_facts``):
each meter reads one value at one position, the group's tapered value of the
side minus the opponent's, in pawns, from that side's view
(``health_values``), mapped linearly by that group's fitted ``HealthScale``.
``Overall`` reads the guide's main evaluation without tempo. A position where
both sides have exchanged the same material reads healthy for both: the
values are net, never measured against the starting position.

This module only adds what the panel shows: names, the level word, the trend
against the previous position and each meter's largest terms. A term is a
part of the group, net and from the side's view, so the terms of a meter add
up to the value it reads; the overall meter's terms are the groups.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import chess

from scan64.explanations.factor_facts import (
    GROUP_NAMES,
    HEALTH_GROUPS,
    HEALTH_SCALE,
    health_values,
)
from scan64.explanations.factors import evaluate_factors
from scan64.explanations.factors.report import PAWN_VALUE_EG, FactorReport

Side = Literal["white", "black"]
Level = Literal["good", "fair", "low"]
Trend = Literal["rising", "falling", "steady"]

# Health at or above ``good`` reads good, below ``fair`` reads low.
LEVELS: Mapping[Level, float] = {"good": 2 / 3, "fair": 1 / 3}
# A health change smaller than this reads steady.
TREND_STEP = 0.05
# Terms shown in a meter's details view, largest first.
DETAIL_TERMS = 3
# Panel names: the narrator's plain group names, plus the groups it never names.
PANEL_NAMES: Mapping[str, str] = {**GROUP_NAMES, "Material": "material", "Overall": "overall"}


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


@dataclass(frozen=True)
class PositionHealth:
    """The overall meter and one meter per group, for both sides."""

    overall: GroupReading
    groups: tuple[GroupReading, ...]


def level(value: float) -> Level:
    if value >= LEVELS["good"]:
        return "good"
    return "fair" if value >= LEVELS["fair"] else "low"


def trend(now: float, before: float | None) -> Trend:
    if before is None or abs(now - before) < TREND_STEP:
        return "steady"
    return "rising" if now > before else "falling"


def _color(side: Side) -> chess.Color:
    return chess.WHITE if side == "white" else chess.BLACK


def _terms(report: FactorReport, key: str, side: Side) -> tuple[TermReading, ...]:
    """The largest nonzero net terms of ``side``'s meter, pawns, ``side``'s view."""
    sign = 1 if side == "white" else -1
    if key == "Overall":
        readings = [
            TermReading(name, sign * report.groups[name].tapered / PAWN_VALUE_EG)
            for name in HEALTH_GROUPS
        ]
    else:
        readings = [
            TermReading(part.name, sign * part.tapered / PAWN_VALUE_EG)
            for part in report.contributions
            if part.group == key
        ]
    largest = sorted(readings, key=lambda r: abs(r.pawns), reverse=True)
    rounded = (TermReading(r.name, round(r.pawns, 2)) for r in largest)
    return tuple(r for r in rounded if r.pawns != 0)[:DETAIL_TERMS]


def _meter(
    report: FactorReport, previous: FactorReport | None, key: str, side: Side
) -> MeterReading:
    scale = HEALTH_SCALE[key]
    now = scale.health(health_values(report, _color(side))[key])
    before = None if previous is None else scale.health(health_values(previous, _color(side))[key])
    return MeterReading(round(now, 3), level(now), trend(now, before), _terms(report, key, side))


def _reading(
    report: FactorReport, previous: FactorReport | None, key: str, learner: Side
) -> GroupReading:
    opponent: Side = "black" if learner == "white" else "white"
    return GroupReading(
        key,
        PANEL_NAMES[key],
        _meter(report, previous, key, learner),
        _meter(report, previous, key, opponent),
    )


def position_health(
    board: chess.Board, learner: Side, previous: chess.Board | None = None
) -> PositionHealth:
    """Every meter for ``learner`` and the opponent; ``previous`` gives the trend."""
    report = evaluate_factors(board)
    before = None if previous is None else evaluate_factors(previous)
    return PositionHealth(
        overall=_reading(report, before, "Overall", learner),
        groups=tuple(_reading(report, before, key, learner) for key in HEALTH_GROUPS),
    )
