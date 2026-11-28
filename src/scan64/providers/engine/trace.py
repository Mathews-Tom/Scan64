"""Evaluation traces: the breakdown a classical engine prints for ``eval``.

A profile declares its trace in a ``[trace]`` table (``format`` and optional
``options``). Without one, ``read_eval_trace`` raises
``EvalTraceUnavailableError``: Scan64 never assumes an engine has a trace.

The trace runs in its own short-lived process, not in the analysis pools: it
is a static evaluation with no search, and the format's options (``Use NNUE``
false) must not reach the analysis engine.

Formats and the engines that print them:

| Format | Engines | Units | Stream |
|---|---|---|---|
| ``stockfish`` | Stockfish 2.1 to 15.1, Fairy-Stockfish | pawns | stdout |
| ``rubichess`` | RubiChess with ``Use_NNUE`` false | pawns | stdout |
| ``vajolet`` | Vajolet2 | pawns | stdout |
| ``rodent`` | Rodent V | centipawns | stdout |
| ``laser`` | Laser | centipawns | stderr |

Stockfish 12 to 15.1 and Fairy-Stockfish print the classical table only with
``Use NNUE`` false; the runner sets it when the engine declares it.

Every value here is in pawns from White's point of view, as the engines print
their tables. Group names follow the factor module (``Material``, ``Pieces``,
``King`` and the others); rows with no factor group keep their own name only.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import chess

from scan64.providers.engine.config import (
    EngineConfigurationError,
    EngineProfile,
    TraceFormatName,
    UciOptionValue,
)
from scan64.providers.engine.uci import EngineCapabilityError

# A trace is one static evaluation: the handshake and ``eval`` together take
# milliseconds. The bound covers a slow engine start (Rodent V loads its data).
TRACE_TIMEOUT_S = 10.0
# Bytes read from the engine; a classical trace is about 4 KB.
MAX_TRACE_BYTES = 256 * 1024


class EvalTraceUnavailableError(EngineCapabilityError):
    """The engine profile declares no evaluation trace, or the engine printed none."""

    kind = "no_eval_trace"


@dataclass(frozen=True, slots=True)
class TraceRow:
    """One row of the printed table, in pawns; ``None`` where the engine prints dashes."""

    name: str
    white_mg: float | None
    white_eg: float | None
    black_mg: float | None
    black_eg: float | None
    total_mg: float
    total_eg: float | None


@dataclass(frozen=True, slots=True)
class EvalTrace:
    """An engine's evaluation breakdown of one position.

    ``source`` is the engine's ``id name`` (its name and version). The values
    are the engine's own and stay apart from the factor module's.
    ``evaluation`` is the final evaluation in pawns for White, when the engine
    prints it as such.
    """

    source: str
    format: TraceFormatName
    fen: str
    rows: tuple[TraceRow, ...]
    evaluation: float | None

    def groups(self) -> Mapping[str, tuple[float, float | None]]:
        """Net (White minus Black) middle-game and end-game value per factor group."""
        sums: dict[str, tuple[float, float | None]] = {}
        for row in self.rows:
            group = TRACE_GROUPS.get(_key(row.name))
            if group is None:
                continue
            mg, eg = sums.get(group, (0.0, 0.0))
            sums[group] = (
                mg + row.total_mg,
                None if eg is None or row.total_eg is None else eg + row.total_eg,
            )
        return MappingProxyType(sums)


# Printed row name (lower case, single spaces) -> factor module group. The
# trace's own ``Total`` rows map to none.
TRACE_GROUPS: Mapping[str, str] = MappingProxyType(
    {
        "material": "Material",
        "material, pst, tempo": "Material",
        "pst": "Material",
        "psqt": "Material",
        "imbalance": "Imbalance",
        "material imbalance": "Imbalance",
        "imbalancies": "Imbalance",
        "pawns": "Pawns",
        "knights": "Pieces",
        "bishop": "Pieces",
        "bishops": "Pieces",
        "rooks": "Pieces",
        "queens": "Pieces",
        "minors": "Pieces",
        "pieces": "Pieces",
        "mobility": "Mobility",
        "threats": "Threats",
        "threat": "Threats",
        "passed": "Passed pawns",
        "passed pawns": "Passed pawns",
        "passers": "Passed pawns",
        "space": "Space",
        "king safety": "King",
        "safety": "King",
        "king attacks": "King",
        "initiative": "Winnable",
        "winnable": "Winnable",
    }
)

# Options each format sets for the trace when the engine declares them: the
# classical table needs the hand-written evaluation.
FORMAT_OPTIONS: Mapping[TraceFormatName, Mapping[str, UciOptionValue]] = MappingProxyType(
    {
        "stockfish": {"Use NNUE": False},
        "rubichess": {"Use_NNUE": False},
        "vajolet": {},
        "rodent": {},
        "laser": {},
    }
)
# Laser prints its table to standard error.
STDERR_FORMATS = frozenset({"laser"})
# Divisor from the printed unit to pawns.
_UNIT: Mapping[TraceFormatName, float] = MappingProxyType(
    {"stockfish": 1.0, "rubichess": 1.0, "vajolet": 1.0, "rodent": 100.0, "laser": 100.0}
)


def _key(name: str) -> str:
    return " ".join(name.lower().split())


def _number(token: str) -> float | None:
    """A printed value; ``None`` for the dash placeholders. ``ValueError`` otherwise."""
    if token and set(token) == {"-"}:
        return None
    return float(token)


def _pair(cell: str) -> tuple[float | None, float | None] | None:
    """The first two values of a cell, or ``None`` when it has fewer."""
    values: list[float | None] = []
    for token in cell.split():
        try:
            values.append(_number(token))
        except ValueError:
            break
        if len(values) == 2:
            return values[0], values[1]
    return None


def _is_rule(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= {"-", "+"}


def _table_rows(text: str, unit: float) -> tuple[TraceRow, ...]:
    """Rows of the first ``|`` table whose header names White and Black."""
    rows: list[TraceRow] = []
    started = False
    for line in text.splitlines():
        if not started:
            started = "|" in line and "White" in line and "Black" in line
            continue
        if _is_rule(line):
            continue
        if "|" not in line:
            if rows:
                break
            continue
        cells = line.rstrip().split("|")
        # A ``|`` in the first column is a table border (Stockfish 12 and
        # later); Laser's total row starts with spaces and an empty name.
        if line.startswith("|"):
            cells = cells[1:]
        if cells and not cells[-1].strip():
            cells = cells[:-1]
        if len(cells) < 2:
            continue
        total = _pair(cells[-1])
        if total is None or total[0] is None:
            continue
        sides = [_pair(cell) or (None, None) for cell in cells[1:-1]]
        white, black = (sides + [(None, None), (None, None)])[:2]
        name = cells[0].strip() or "Total"

        def scaled(value: float | None) -> float | None:
            return None if value is None else value / unit

        rows.append(
            TraceRow(
                name=name,
                white_mg=scaled(white[0]),
                white_eg=scaled(white[1]),
                black_mg=scaled(black[0]),
                black_eg=scaled(black[1]),
                total_mg=total[0] / unit,
                total_eg=scaled(total[1]),
            )
        )
    return tuple(rows)


_RODENT_ROW = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z ]*?)\s+"
    r"(?P<v>-?\d+(?:\s+-?\d+){6})\s*$"
)
_RODENT_TOTAL = re.compile(r"^TOTAL\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*$")


def _rodent_rows(text: str) -> tuple[TraceRow, ...]:
    """Rodent V's ``Evaluation breakdown``: MG W, MG B, MG diff, EG W, EG B, EG diff, Blend."""
    rows: list[TraceRow] = []
    started = False
    for line in text.splitlines():
        if line.startswith("Evaluation breakdown"):
            started = True
            continue
        if not started:
            continue
        total = _RODENT_TOTAL.match(line)
        if total:
            mg, eg, _ = (int(v) / 100 for v in total.groups())
            rows.append(TraceRow("TOTAL", None, None, None, None, mg, eg))
            break
        match = _RODENT_ROW.match(line)
        if match:
            mgw, mgb, mgd, egw, egb, egd, _ = (int(v) / 100 for v in match["v"].split())
            rows.append(TraceRow(match["name"].strip(), mgw, egw, mgb, egb, mgd, egd))
    return tuple(rows)


_WHITE_SIDE = re.compile(r"^(Final|Total) [Ee]valuation:?\s+([+-]?\d+(?:\.\d+)?) \(white side\)")
_RODENT_WHITE = re.compile(r"^From White's point of view:\s*(-?\d+) cp")


def _evaluation(trace_format: TraceFormatName, text: str) -> float | None:
    """The final evaluation for White, where the engine labels it so."""
    found: dict[str, float] = {}
    for line in text.splitlines():
        if trace_format == "rodent":
            match = _RODENT_WHITE.match(line.strip())
            if match:
                return int(match[1]) / 100
        else:
            match = _WHITE_SIDE.match(line.strip())
            if match:
                found[match[1]] = float(match[2])
    return found.get("Final", found.get("Total"))


def parse_eval_trace(
    trace_format: TraceFormatName, text: str, *, source: str, fen: str
) -> EvalTrace:
    """Read one ``eval`` printout. Raise ``EvalTraceUnavailableError`` without a table."""
    if trace_format == "rodent":
        rows = _rodent_rows(text)
    else:
        rows = _table_rows(text, _UNIT[trace_format])
    if not rows:
        raise EvalTraceUnavailableError(
            f"{source} printed no {trace_format} evaluation table for the eval command"
        )
    return EvalTrace(
        source=source,
        format=trace_format,
        fen=fen,
        rows=rows,
        evaluation=_evaluation(trace_format, text),
    )


def _option_value(value: UciOptionValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def trace_options(
    profile_name: str,
    trace_format: TraceFormatName,
    extra: Mapping[str, UciOptionValue],
    declared: frozenset[str],
) -> dict[str, UciOptionValue]:
    """The format's defaults the engine declares, then the profile's ``[trace.options]``."""
    options = {
        name: value for name, value in FORMAT_OPTIONS[trace_format].items() if name in declared
    }
    for name, value in extra.items():
        if name not in declared:
            raise EngineConfigurationError(
                f"Engine profile {profile_name!r} sets trace option {name!r}, "
                "which the engine does not declare"
            )
        options[name] = value
    return options


_OPTION_NAME = re.compile(r"^option name (.+?) type ")


async def _read_until_uciok(stream: asyncio.StreamReader) -> tuple[list[str], str | None]:
    lines: list[str] = []
    name: str | None = None
    size = 0
    while True:
        raw = await stream.readline()
        if not raw:
            raise EvalTraceUnavailableError("The trace engine exited before uciok")
        size += len(raw)
        if size > MAX_TRACE_BYTES:
            raise EvalTraceUnavailableError("The trace engine's handshake is too long")
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        lines.append(line)
        if line.startswith("id name "):
            name = line[len("id name ") :].strip()
        if line.strip() == "uciok":
            return lines, name


def _cwd(binary_path: str) -> str | None:
    """Engines such as Rodent V read data files relative to their own folder."""
    if os.sep in binary_path or (os.altsep is not None and os.altsep in binary_path):
        return str(Path(binary_path).parent)
    return None


async def read_eval_trace(
    profile: EngineProfile, board: chess.Board, *, timeout_s: float = TRACE_TIMEOUT_S
) -> EvalTrace:
    """Start the profile's engine, send ``eval`` on ``board`` and read its trace.

    Raises ``EvalTraceUnavailableError`` when the profile declares no trace, for
    a position the trace does not cover (variants, a side in check), and when
    the engine prints no table; ``EngineConfigurationError`` for a trace option
    the engine does not declare; ``TimeoutError`` past ``timeout_s``.
    """
    settings = profile.trace
    if settings is None:
        raise EvalTraceUnavailableError(
            f"Engine profile {profile.name!r} declares no evaluation trace. "
            "Add a [trace] table naming its format, for an engine that prints one."
        )
    if profile.protocol != "uci":
        raise EvalTraceUnavailableError("Evaluation traces are read over UCI only")
    if type(board) is not chess.Board:
        raise EvalTraceUnavailableError(
            f"Evaluation traces cover standard chess, not {type(board).uci_variant}"
        )
    if board.is_check():
        raise EvalTraceUnavailableError("Engines print no evaluation trace with a side in check")
    process = await asyncio.create_subprocess_exec(
        profile.binary_path,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=_cwd(profile.binary_path),
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        async with asyncio.timeout(timeout_s):
            process.stdin.write(b"uci\n")
            await process.stdin.drain()
            handshake, engine_name = await _read_until_uciok(process.stdout)
            declared = frozenset(
                match[1] for line in handshake if (match := _OPTION_NAME.match(line))
            )
            options = trace_options(profile.name, settings.format, settings.options, declared)
            commands = [f"setoption name {n} value {_option_value(v)}" for n, v in options.items()]
            commands += [f"position fen {board.fen()}", "eval", "quit"]
            process.stdin.write(("\n".join(commands) + "\n").encode())
            await process.stdin.drain()
            process.stdin.close()
            out, err = await process.communicate()
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
    raw = err if settings.format in STDERR_FORMATS else out
    text = raw[:MAX_TRACE_BYTES].decode("utf-8", "replace")
    return parse_eval_trace(
        settings.format, text, source=engine_name or profile.name, fen=board.fen()
    )
