"""Fit and check the factor-fact thresholds and the health scale (issues #410, #416).

Usage:
    uv run python -m benchmarks.factor_calibration.calibrate          # print the report tables
    uv run python -m benchmarks.factor_calibration.calibrate --check  # tables == the fit

Position sets (``SETS``):

- ``benchmark``: the 50 narrator-benchmark positions (recorded Stockfish searches);
- ``caissa``: the same games and moves with Caissa 2.0 evidence (``positions-caissa.json``);
- ``slow``: mistakes from Lichess games lost slowly (``positions.json`` in this directory);
- ``null``: moves from the same games that the engine scores as equal to its best move.

Every position is measured as the pipeline measures it: ``build_narrator_facts``
picks the lines and the quiet points, and the factor module is evaluated at
each point. A position with no trusted diagnosis uses a placeholder class code;
the class does not change the factor facts.

Fit (``fit_thresholds``): a null move changes the engine score by at most 25 cp,
so a factor change there is noise for a mistake explanation. For each group and
for the pooled terms, the threshold is quantile ``q`` of the absolute null
changes (both comparisons), raised to the next ``STEP`` multiple strictly above
it, and not below ``FLOOR``. A term whose own null quantile is above the pooled term threshold
gets its own value. ``choose_quantile`` takes the ``q`` in ``QUANTILES`` with the
largest separation: the share of mistakes (``benchmark`` and ``slow``) with at
least one fact minus the share of null moves with one. ``floor_table`` repeats
the choice for each floor in ``FLOORS``; ``FLOOR`` is the one with the largest
separation.

Health scale (``fit_health``, #416): the position health meters read one value
per factor group at one position (``health_values``: the group's tapered value
of a side minus the opponent's, that side's view). Samples are quiet positions,
labelled from one side's view:

- healthy: every quiet measured point of the ``null`` set (the mover's view),
  and quiet positions of the slow-loss games (``games.json``, from ply
  ``GAME_FROM_PLY``) where that side's Lichess eval is within ``+-LEVEL_CP``;
- trouble: the ``played`` point of every ``slow`` mistake (the loser's view),
  and quiet game positions where that side's Lichess eval is ``TROUBLE_CP`` or
  less.

A game position is quiet by the rule of ``quiet_ply`` with the game's next move
as the line. ``full`` is quantile ``HEALTH_FULL_QUANTILE`` of the healthy
values, ``empty`` quantile ``HEALTH_EMPTY_QUANTILE`` of the values in trouble,
each rounded outward to ``STEP`` and at least ``HEALTH_MIN`` from 0. Both sides
of a level game position are healthy samples, so the fit serves both meters.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import chess
from chess_lesson_spec import Diagnosis

from scan64.explanations.factor_facts import (
    FACTOR_CONFIG,
    GROUP_NAMES,
    HEALTH_GROUPS,
    HEALTH_SCALE,
    TERM_NAMES,
    FactorChange,
    FactorConfig,
    FactorKind,
    HealthScale,
    _learner_values,
    health_values,
    passed_changes,
)
from scan64.explanations.factors import PARTS, evaluate_factors
from scan64.explanations.facts import build_narrator_facts
from scan64.learning.evidence.models import Evidence

REPO_ROOT = Path(__file__).resolve().parents[2]
SETS: Mapping[str, Path] = {
    "benchmark": REPO_ROOT / "benchmarks/narrator/positions.json",
    "caissa": REPO_ROOT / "benchmarks/narrator/positions-caissa.json",
    "slow": REPO_ROOT / "benchmarks/factor_calibration/positions.json",
    "null": REPO_ROOT / "benchmarks/factor_calibration/positions.json",
}
PLACEHOLDER_CODE = "threat_processing.missed_capture"
SLOW_CLASS = "slow_loss (no tactical diagnosis)"
QUANTILES = (0.8, 0.85, 0.9, 0.95, 0.975)
STEP = 0.05
FLOOR = 0.10
# Groups a factor fact names for each class when it names the class's cause.
EXPECTED: Mapping[str, frozenset[str]] = {
    "threat_processing.allowed_mate": frozenset({"King", "King threat"}),
    "threat_processing.missed_check": frozenset({"King", "King threat"}),
    "threat_processing.missed_capture": frozenset({"Threats"}),
    "board_awareness.hanging_piece": frozenset({"Threats"}),
    "threat_processing.missed_direct_threat": frozenset({"Threats"}),
    "tactics.fork.knight": frozenset({"Threats"}),
    SLOW_CLASS: frozenset({"Pawns", "Pieces", "Mobility", "Passed pawns", "Space", "King"}),
}
_GROUP_OF = dict(PARTS)
# The #408 values the fit replaced.
PRE_410_CONFIG = replace(FACTOR_CONFIG, thresholds={"group": 0.30, "term": 0.15}, overrides={})

Values = Mapping[tuple[FactorKind, str], float]


@dataclass(frozen=True)
class Measurement:
    set: str
    position_id: str
    fen: str
    code: str
    swing_cp: int | None
    played: Values | None
    before: Values
    best: Values | None
    points: Mapping[str, tuple[str, ...]]


def _samples(name: str) -> list[dict[str, Any]]:
    data = json.loads(SETS[name].read_text(encoding="utf-8"))
    positions: list[dict[str, Any]] = data["positions"]
    if name in ("slow", "null"):
        return [p for p in positions if p["set"] == name]
    return positions


def measure(name: str, sample: Mapping[str, Any]) -> Measurement | None:
    """Factor values at the pipeline's three points; ``None`` without factor facts."""
    evidence = [Evidence(**item) for item in sample["evidence"]]
    raw = sample["diagnosis"] or {
        "primary": PLACEHOLDER_CODE,
        "secondary": [],
        "confidence": 0.0,
        "evidence_refs": [evidence[0].evidence_id],
    }
    facts = build_narrator_facts(Diagnosis(**raw), evidence, sample["fen"])
    if facts.factors is None:
        return None
    before = chess.Board(sample["fen"])
    learner = before.turn
    values: dict[str, Values | None] = {}
    points: dict[str, tuple[str, ...]] = {}
    for point in facts.factors.points:
        points[point.point] = point.uci
        if not point.quiet:
            values[point.point] = None
            continue
        board = before.copy(stack=False)
        for token in point.uci:
            board.push(chess.Move.from_uci(token))
        values[point.point] = _learner_values(evaluate_factors(board), learner)
    code = sample["diagnosis"]["primary"] if sample["diagnosis"] else SLOW_CLASS
    before_values = values["before"]
    assert before_values is not None
    return Measurement(
        set=name,
        position_id=sample["position_id"],
        fen=sample["fen"],
        code=code,
        swing_cp=facts.factors.engine_swing_cp,
        played=values.get("played"),
        before=before_values,
        best=values.get("best"),
        points=points,
    )


def measure_all(names: Sequence[str] = tuple(SETS)) -> list[Measurement]:
    measured = []
    for name in names:
        for sample in _samples(name):
            if (found := measure(name, sample)) is not None:
                measured.append(found)
    return measured


def deltas(measurement: Measurement) -> dict[str, dict[tuple[FactorKind, str], float]]:
    """``played - reference`` per comparison, every group and term, learner's view."""
    if measurement.played is None:
        return {}
    result = {}
    for name, reference in (("before", measurement.before), ("best", measurement.best)):
        if reference is not None:
            result[name] = {
                key: value - reference.get(key, 0.0) for key, value in measurement.played.items()
            }
    return result


def _group(kind: FactorKind, guide_name: str) -> str:
    return guide_name if kind == "group" else _GROUP_OF[guide_name]


def _stated(kind: FactorKind, guide_name: str, config: FactorConfig) -> bool:
    return _group(kind, guide_name) not in config.excluded_groups


def _fit_value(changes: Sequence[float], quantile: float, floor: float = FLOOR) -> float:
    """The smallest ``STEP`` multiple, at least ``FLOOR``, that at most ``1 - quantile``
    of the null changes reach (the size rule passes ``abs(change) >= threshold``)."""
    allowed = (1 - quantile) * len(changes) + 1e-9
    steps = round(floor / STEP)
    while True:
        threshold = round(steps * STEP, 2)
        if sum(change >= threshold - 1e-9 for change in changes) <= allowed:
            return threshold
        steps += 1


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    rank = fraction * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def null_changes(measured: Sequence[Measurement]) -> dict[tuple[FactorKind, str], list[float]]:
    pooled: dict[tuple[FactorKind, str], list[float]] = {}
    for measurement in measured:
        if measurement.set != "null":
            continue
        for changes in deltas(measurement).values():
            for key, change in changes.items():
                pooled.setdefault(key, []).append(abs(change))
    return pooled


def fit_thresholds(
    measured: Sequence[Measurement], quantile: float, floor: float = FLOOR
) -> dict[str, Any]:
    """Thresholds from the null quantiles (module docstring)."""
    pooled = null_changes(measured)
    groups = {
        name: _fit_value(changes, quantile, floor)
        for (kind, name), changes in sorted(pooled.items())
        if kind == "group" and _stated(kind, name, FACTOR_CONFIG)
    }
    term_values = [
        change
        for (kind, name), changes in pooled.items()
        if kind == "term" and _stated(kind, name, FACTOR_CONFIG)
        for change in changes
    ]
    term = _fit_value(term_values, quantile, floor)
    terms = {
        name: fitted
        for (kind, name), changes in sorted(pooled.items())
        if kind == "term"
        and _stated(kind, name, FACTOR_CONFIG)
        and (fitted := _fit_value(changes, quantile, floor)) > term
    }
    return {"quantile": quantile, "term": term, "groups": groups, "terms": terms}


def fire_rate(measured: Sequence[Measurement], sets: Sequence[str], config: FactorConfig) -> float:
    group = [m for m in measured if m.set in sets]
    return sum(bool(fired(m, config)) for m in group) / len(group)


def separation(measured: Sequence[Measurement], config: FactorConfig) -> float:
    return fire_rate(measured, ("benchmark", "slow"), config) - fire_rate(
        measured, ("null",), config
    )


def choose_quantile(measured: Sequence[Measurement], floor: float = FLOOR) -> dict[str, Any]:
    """The fit with the largest separation over ``QUANTILES`` (first on a tie)."""
    fits = [fit_thresholds(measured, q, floor) for q in QUANTILES]
    return max(fits, key=lambda fit: separation(measured, fitted_config(fit)))


FLOORS = (0.10, 0.15, 0.20, 0.25)


def floor_table(measured: Sequence[Measurement]) -> str:
    """Each floor in ``FLOORS`` with its own best quantile."""
    lines = [
        "| Floor (pawns) | Quantile | Mistakes with a fact | Null moves with a fact | Separation "
        "| Class match (positions with a fact) |",
        "|---|---|---|---|---|---|",
    ]
    for floor in FLOORS:
        fit = choose_quantile(measured, floor)
        config = fitted_config(fit)
        with_fact = [
            (m, facts)
            for m in measured
            if m.set in ("benchmark", "slow") and (facts := fired(m, config))
        ]
        match = sum(any(_matches(c, m.code) for c in facts) for m, facts in with_fact)
        lines.append(
            f"| {floor:.2f} | {fit['quantile']} "
            f"| {fire_rate(measured, ('benchmark', 'slow'), config):.0%} "
            f"| {fire_rate(measured, ('null',), config):.0%} "
            f"| {separation(measured, config):+.2f} | {match / len(with_fact):.0%} |"
        )
    return "\n".join(lines) + "\n"


def sweep_table(measured: Sequence[Measurement]) -> str:
    lines = [
        "| Null quantile | Mistakes with a fact | Null moves with a fact | Separation "
        "| King safety | Threats | Term default |",
        "|---|---|---|---|---|---|---|",
    ]
    for q in QUANTILES:
        fit = fit_thresholds(measured, q)
        config = fitted_config(fit)
        lines.append(
            f"| {q} | {fire_rate(measured, ('benchmark', 'slow'), config):.0%} "
            f"| {fire_rate(measured, ('null',), config):.0%} "
            f"| {separation(measured, config):+.2f} | {fit['groups']['King']} "
            f"| {fit['groups']['Threats']} | {fit['term']} |"
        )
    return "\n".join(lines) + "\n"


def fitted_config(fit: Mapping[str, Any]) -> FactorConfig:
    """``FACTOR_CONFIG`` with the fitted values (the group default is the smallest group value)."""
    groups: Mapping[str, float] = fit["groups"]
    group_default = min(groups.values())
    overrides = {
        **{name: value for name, value in groups.items() if value != group_default},
        **fit["terms"],
    }
    return replace(
        FACTOR_CONFIG,
        thresholds={"group": group_default, "term": fit["term"]},
        overrides=overrides,
    )


def fired(measurement: Measurement, config: FactorConfig) -> list[FactorChange]:
    """The facts the pipeline states for this position under ``config``."""
    if measurement.played is None or measurement.swing_cp is None:
        return []
    changes: list[FactorChange] = []
    for reference, values in (("before", measurement.before), ("best", measurement.best)):
        if values is not None:
            changes += passed_changes(
                measurement.played,
                values,
                measurement.swing_cp,
                reference,  # type: ignore[arg-type]
                config,
            )
    return changes


def _threshold(kind: FactorKind, name: str, config: FactorConfig) -> float:
    return config.threshold(kind, name)


def above_threshold(
    measurement: Measurement, config: FactorConfig
) -> list[tuple[str, FactorKind, str, float]]:
    """Changes that pass the size rule, before the sign rule: (comparison, kind, name, change)."""
    found = []
    for comparison, changes in deltas(measurement).items():
        for (kind, name), change in changes.items():
            if _stated(kind, name, config) and abs(change) >= _threshold(kind, name, config):
                found.append((comparison, kind, name, change))
    return found


def _matches(change: FactorChange, code: str) -> bool:
    expected = EXPECTED[code]
    return change.guide_name in expected or (
        change.kind == "term" and _GROUP_OF[change.guide_name] in expected
    )


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "—"


def report(measured: Sequence[Measurement], config: FactorConfig) -> str:
    """Markdown tables for docs/evaluation/factor-calibration.md."""
    lines: list[str] = []
    order = ["benchmark", "slow", "caissa", "null"]
    lines += [
        "| Set | Measured | Played point quiet | Best point quiet | Swing known |",
        "|---|---|---|---|---|",
    ]
    for name in order:
        group = [m for m in measured if m.set == name]
        lines.append(
            f"| {name} | {len(group)} | {sum(m.played is not None for m in group)} "
            f"| {sum(m.best is not None for m in group)} "
            f"| {sum(m.swing_cp not in (None, 0) for m in group)} |"
        )
    lines += ["", "Position-level fire rate (at least one fact):", ""]
    lines += ["| Set | #408 thresholds | Fitted thresholds |", "|---|---|---|"]
    for name in order:
        group = [m for m in measured if m.set == name]
        old = sum(bool(fired(m, PRE_410_CONFIG)) for m in group)
        new = sum(bool(fired(m, config)) for m in group)
        lines.append(f"| {name} | {_pct(old, len(group))} | {_pct(new, len(group))} |")
    lines += ["", "Per diagnosis class (benchmark and slow sets, fitted thresholds):", ""]
    lines += [
        "| Class | Positions | With a fact | Class match (any fact) | Class match (largest fact) "
        "| Size-rule changes whose sign agrees with the swing |",
        "|---|---|---|---|---|---|",
    ]
    for code in EXPECTED:
        group = [m for m in measured if m.set in ("benchmark", "slow") and m.code == code]
        if not group:
            continue
        facts = {m.position_id: fired(m, config) for m in group}
        with_fact = [m for m in group if facts[m.position_id]]
        any_match = sum(any(_matches(c, code) for c in facts[m.position_id]) for m in with_fact)
        top_match = sum(
            _matches(max(facts[m.position_id], key=lambda c: abs(c.change_pawns)), code)
            for m in with_fact
        )
        sized = [
            (change, m.swing_cp)
            for m in group
            if m.swing_cp
            for _, _, _, change in above_threshold(m, config)
        ]
        agree = sum((change > 0) == (swing > 0) for change, swing in sized if swing is not None)
        lines.append(
            f"| `{code}` | {len(group)} | {_pct(len(with_fact), len(group))} "
            f"| {_pct(any_match, len(with_fact))} | {_pct(top_match, len(with_fact))} "
            f"| {_pct(agree, len(sized))} |"
        )
    lines += ["", "Facts per position (fitted thresholds, both comparisons, at most 6):", ""]
    lines += ["| Set | 0 | 1 | 2 | 3 | 4 | 5 | 6 | Mean |", "|---|---|---|---|---|---|---|---|---|"]
    for name in order:
        group = [m for m in measured if m.set == name]
        counts = Counter(len(fired(m, config)) for m in group)
        mean = statistics.fmean(len(fired(m, config)) for m in group) if group else 0.0
        lines.append(
            f"| {name} | " + " | ".join(str(counts[n]) for n in range(7)) + f" | {mean:.2f} |"
        )
    lines += ["", "Most frequent facts (benchmark and slow sets, fitted thresholds):", ""]
    named = Counter(
        (m.set, change.name)
        for m in measured
        if m.set in ("benchmark", "slow")
        for change in fired(m, config)
    )
    lines += ["| Set | Fact name | Facts |", "|---|---|---|"]
    for name in ("benchmark", "slow"):
        top = sorted(
            ((count, fact) for (set_name, fact), count in named.items() if set_name == name),
            reverse=True,
        )[:8]
        lines += [f"| {name} | {fact} | {count} |" for count, fact in top]
    return "\n".join(lines) + "\n"


def null_table(measured: Sequence[Measurement], fit: Mapping[str, Any]) -> str:
    pooled = null_changes(measured)
    lines = [
        "| Group or term | Kind | Null samples | Null median | Null 90th percentile | Fitted "
        "| #408 |",
        "|---|---|---|---|---|---|---|",
    ]
    for (kind, name), changes in sorted(pooled.items(), key=lambda item: (item[0][0], item[0][1])):
        if not _stated(kind, name, FACTOR_CONFIG):
            continue
        if kind == "group":
            fitted = f"{fit['groups'][name]:.2f}"
        elif name in fit["terms"]:
            fitted = f"{fit['terms'][name]:.2f}"
        else:
            fitted = f"{fit['term']:.2f} (term default)"
        if kind == "term" and name not in fit["terms"] and max(changes) == 0:
            continue
        plain = GROUP_NAMES.get(name) or TERM_NAMES[name]
        lines.append(
            f"| {name} ({plain}) | {kind} | {len(changes)} | {statistics.median(changes):.2f} "
            f"| {_quantile(changes, fit['quantile']):.2f} | {fitted} "
            f"| {PRE_410_CONFIG.thresholds[kind]:.2f} |"
        )
    return "\n".join(lines) + "\n"


# --- Health scale (#416) -------------------------------------------------------------

GAMES = REPO_ROOT / "benchmarks/factor_calibration/games.json"
HEALTH_FULL_QUANTILE = 0.8
HEALTH_EMPTY_QUANTILE = 0.2
HEALTH_MIN = 0.10
LEVEL_CP = 100
TROUBLE_CP = -300
GAME_FROM_PLY = 10
HealthLabel = Literal["healthy", "trouble"]


@dataclass(frozen=True)
class HealthSample:
    """One side's meter values at one quiet position, with its label."""

    label: HealthLabel
    source: str
    to_move: bool
    values: Mapping[str, float]


def _game_quiet(board: chess.Board, move: chess.Move) -> bool:
    """The rule of ``quiet_ply`` with the game's next move as the line."""
    return not (board.is_check() or board.is_capture(move) or board.gives_check(move))


def health_samples(measured: Sequence[Measurement]) -> list[HealthSample]:
    """Labelled meter values (module docstring, "Health scale")."""
    samples: list[HealthSample] = []
    for m in measured:
        if m.set not in ("null", "slow"):
            continue
        root = chess.Board(m.fen)
        for point, uci in sorted(m.points.items()):
            if getattr(m, point) is None or (m.set == "slow" and point != "played"):
                continue
            board = root.copy(stack=False)
            for token in uci:
                board.push(chess.Move.from_uci(token))
            label: HealthLabel = "healthy" if m.set == "null" else "trouble"
            samples.append(
                HealthSample(
                    label,
                    f"{m.set} {point}",
                    board.turn == root.turn,
                    health_values(evaluate_factors(board), root.turn),
                )
            )
    games = json.loads(GAMES.read_text(encoding="utf-8"))["games"]
    for game in games:
        board = chess.Board()
        for ply, san in enumerate(game["moves"]):
            move = board.parse_san(san)
            if ply >= GAME_FROM_PLY and _game_quiet(board, move):
                # lichess_white_cp[k]: White's view after ply k + 1.
                white_cp = game["lichess_white_cp"][ply - 1]
                report = evaluate_factors(board)
                for side in (chess.WHITE, chess.BLACK):
                    cp = white_cp if side == chess.WHITE else -white_cp
                    if abs(cp) <= LEVEL_CP:
                        label = "healthy"
                    elif cp <= TROUBLE_CP:
                        label = "trouble"
                    else:
                        continue
                    samples.append(
                        HealthSample(label, "game", side == board.turn, health_values(report, side))
                    )
            board.push(move)
    return samples


def _outward(value: float, up: bool) -> float:
    steps = value / STEP
    rounded = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)
    return round(rounded * STEP, 2)


def fit_health(samples: Sequence[HealthSample]) -> dict[str, HealthScale]:
    """``full``: ``HEALTH_FULL_QUANTILE`` of the healthy values, rounded up to ``STEP``
    and at least ``HEALTH_MIN``; ``empty``: ``HEALTH_EMPTY_QUANTILE`` of the values in
    trouble, rounded down and at most ``-HEALTH_MIN``."""
    fitted = {}
    for name in (*HEALTH_GROUPS, "Overall"):
        healthy = [s.values[name] for s in samples if s.label == "healthy"]
        trouble = [s.values[name] for s in samples if s.label == "trouble"]
        full = _outward(_quantile(healthy, HEALTH_FULL_QUANTILE), up=True)
        empty = _outward(_quantile(trouble, HEALTH_EMPTY_QUANTILE), up=False)
        fitted[name] = HealthScale(full=max(full, HEALTH_MIN), empty=min(empty, -HEALTH_MIN))
    return fitted


def _auc(healthy: Sequence[float], trouble: Sequence[float]) -> float:
    """Chance that a healthy value is above a value in trouble (ties count half)."""
    ordered = sorted(trouble)
    total = 0.0
    for value in healthy:
        below = bisect.bisect_left(ordered, value)
        ties = bisect.bisect_right(ordered, value) - below
        total += below + ties / 2
    return total / (len(healthy) * len(trouble))


def health_table(samples: Sequence[HealthSample], scales: Mapping[str, HealthScale]) -> str:
    """Markdown tables for the "Health scale" section of the report."""
    counts = Counter((s.label, s.source) for s in samples)
    lines = ["| Label | Source | Samples |", "|---|---|---|"]
    lines += [f"| {label} | {source} | {n} |" for (label, source), n in sorted(counts.items())]
    lines += [
        "",
        "| Meter | Healthy median | Healthy 80th pct | Trouble median | Trouble 20th pct "
        "| Full | Empty | Start reads | Healthy reads >= 2/3 | Trouble reads < 1/3 | AUC |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, scale in scales.items():
        healthy = [s.values[name] for s in samples if s.label == "healthy"]
        trouble = [s.values[name] for s in samples if s.label == "trouble"]
        good = sum(scale.health(v) >= 2 / 3 for v in healthy) / len(healthy)
        low = sum(scale.health(v) < 1 / 3 for v in trouble) / len(trouble)
        lines.append(
            f"| {name} | {statistics.median(healthy):+.2f} "
            f"| {_quantile(healthy, HEALTH_FULL_QUANTILE):+.2f} "
            f"| {statistics.median(trouble):+.2f} "
            f"| {_quantile(trouble, HEALTH_EMPTY_QUANTILE):+.2f} "
            f"| {scale.full:+.2f} | {scale.empty:+.2f} | {scale.health(0.0):.2f} "
            f"| {good:.0%} | {low:.0%} | {_auc(healthy, trouble):.2f} |"
        )
    lines += [
        "",
        "Side to move (check, not used in the fit):",
        "",
        "| Meter | Full, to move | Empty, to move | Full, waiting | Empty, waiting |",
        "|---|---|---|---|---|",
    ]
    by_side = {
        flag: fit_health([s for s in samples if s.to_move == flag]) for flag in (True, False)
    }
    for name in scales:
        lines.append(
            f"| {name} | {by_side[True][name].full:+.2f} | {by_side[True][name].empty:+.2f} "
            f"| {by_side[False][name].full:+.2f} | {by_side[False][name].empty:+.2f} |"
        )
    return "\n".join(lines) + "\n"


def health_source(scales: Mapping[str, HealthScale]) -> str:
    """``HEALTH_SCALE`` entries as source lines, for pasting after a refit."""
    return "\n".join(
        f'    "{name}": HealthScale(full={scale.full:.2f}, empty={scale.empty:.2f}),'
        for name, scale in scales.items()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if FACTOR_CONFIG or HEALTH_SCALE differs"
    )
    parser.add_argument("--json", type=Path, help="write the fit and per-position facts")
    args = parser.parse_args()
    measured = measure_all()
    fit = choose_quantile(measured)
    config = fitted_config(fit)
    samples = health_samples(measured)
    scales = fit_health(samples)
    if args.check:
        same = dict(config.thresholds) == dict(FACTOR_CONFIG.thresholds) and dict(
            config.overrides
        ) == dict(FACTOR_CONFIG.overrides)
        same_health = scales == dict(HEALTH_SCALE)
        print(json.dumps(fit, indent=1, sort_keys=True))
        print(health_source(scales))
        if not same_health:
            print("HEALTH_SCALE differs from the fit", file=sys.stderr)
        sys.exit(0 if same and same_health else 1)
    print(json.dumps(fit, indent=1, sort_keys=True))
    print(health_source(scales))
    print(health_table(samples, scales))
    print(sweep_table(measured))
    print(floor_table(measured))
    print(null_table(measured, fit))
    print(report(measured, config))
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "fit": fit,
                    "positions": [
                        {
                            "set": m.set,
                            "position_id": m.position_id,
                            "code": m.code,
                            "swing_cp": m.swing_cp,
                            "points": m.points,
                            "facts": [c.model_dump() for c in fired(m, config)],
                        }
                        for m in measured
                    ],
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
