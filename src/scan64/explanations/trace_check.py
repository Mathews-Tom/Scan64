"""Engine evaluation traces next to the factor module, and where they disagree.

A trace (``scan64.providers.engine.trace``) is an extra factor source: the
engine's own breakdown, kept under its engine name and version. It is never
merged into the factor module's values; ``FactorSources`` holds both side by
side. ``sign_disagreements`` lists the groups where the two point opposite
ways, as a cross-check of the factor module.

Run the report on a profile that declares a ``[trace]`` table:

    uv run python -m scan64.explanations.trace_check --profile engine.toml \\
        --fens positions.txt

``positions.txt`` holds one FEN per line; blank lines and ``#`` lines are
skipped. The report is Markdown on standard output.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import chess

from scan64.explanations.factors import PAWN_VALUE_EG, FactorReport, evaluate_factors
from scan64.providers.engine.config import EngineProfile, load_engine_profile
from scan64.providers.engine.trace import EvalTrace, EvalTraceUnavailableError, read_eval_trace

# A value smaller than this, in pawns, has no sign: rounding in the printed
# trace (two decimals) and small weight differences between Stockfish versions
# stay below it.
SIGN_EPSILON = 0.05

type GamePhase = Literal["middle game", "end game"]


@dataclass(frozen=True, slots=True)
class FactorSources:
    """The factor module's report and the engine traces of one position."""

    report: FactorReport
    traces: tuple[EvalTrace, ...]


@dataclass(frozen=True, slots=True)
class SignDisagreement:
    """One group and phase where the trace and the factor module differ in sign (pawns)."""

    fen: str
    source: str
    group: str
    phase: GamePhase
    module: float
    trace: float


def _sign(value: float, epsilon: float) -> int:
    if abs(value) < epsilon:
        return 0
    return 1 if value > 0 else -1


def sign_disagreements(
    report: FactorReport, trace: EvalTrace, *, epsilon: float = SIGN_EPSILON
) -> list[SignDisagreement]:
    """Groups where both sources have a sign and the signs are opposite."""
    found: list[SignDisagreement] = []
    for group, (trace_mg, trace_eg) in trace.groups().items():
        module = report.groups.get(group)
        if module is None:
            continue
        pairs: tuple[tuple[GamePhase, float, float | None], ...] = (
            ("middle game", module.net_mg / PAWN_VALUE_EG, trace_mg),
            ("end game", module.net_eg / PAWN_VALUE_EG, trace_eg),
        )
        for phase, module_value, trace_value in pairs:
            if trace_value is None:
                continue
            a, b = _sign(module_value, epsilon), _sign(trace_value, epsilon)
            if a and b and a != b:
                found.append(
                    SignDisagreement(
                        report.fen, trace.source, group, phase, module_value, trace_value
                    )
                )
    return found


@dataclass(frozen=True, slots=True)
class Skipped:
    fen: str
    reason: str


async def collect(
    profile: EngineProfile, fens: Sequence[str]
) -> tuple[list[FactorSources], list[Skipped]]:
    """The factor report and the profile's trace of each position."""
    sources: list[FactorSources] = []
    skipped: list[Skipped] = []
    for fen in fens:
        board = chess.Board(fen)
        try:
            trace = await read_eval_trace(profile, board)
        except EvalTraceUnavailableError as error:
            skipped.append(Skipped(board.fen(), str(error)))
            continue
        sources.append(FactorSources(evaluate_factors(board), (trace,)))
    return sources, skipped


def render_report(
    sources: Sequence[FactorSources],
    skipped: Sequence[Skipped] = (),
    *,
    epsilon: float = SIGN_EPSILON,
) -> str:
    """Markdown: every position and group where a trace and the module disagree in sign."""
    rows = [
        d
        for item in sources
        for trace in item.traces
        for d in sign_disagreements(item.report, trace, epsilon=epsilon)
    ]
    traced = sorted({trace.source for item in sources for trace in item.traces})
    lines = [
        "# Evaluation trace and factor module: sign disagreements",
        "",
        f"- Trace sources: {', '.join(traced) or 'none'}",
        f"- Positions compared: {len(sources)}",
        f"- Positions with a disagreement: {len({(d.fen, d.source) for d in rows})}",
        f"- Values under {epsilon:.2f} pawns count as no sign.",
        "",
    ]
    if rows:
        lines += [
            "| FEN | Source | Group | Phase | Factor module | Trace |",
            "|---|---|---|---|---:|---:|",
        ]
        lines += [
            f"| `{d.fen}` | {d.source} | {d.group} | {d.phase} | {d.module:+.2f} | {d.trace:+.2f} |"
            for d in rows
        ]
    else:
        lines.append("No group disagrees in sign.")
    if skipped:
        lines += ["", "## Skipped", ""]
        lines += [f"- `{s.fen}`: {s.reason}" for s in skipped]
    return "\n".join(lines) + "\n"


def _fens(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--profile", type=Path, required=True, help="engine TOML with [trace]")
    parser.add_argument("--fens", type=Path, required=True, help="one FEN per line")
    args = parser.parse_args(argv)
    profile = load_engine_profile(args.profile.resolve(), "--profile")
    sources, skipped = asyncio.run(collect(profile, _fens(args.fens)))
    sys.stdout.write(render_report(sources, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
