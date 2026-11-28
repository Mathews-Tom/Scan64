"""Engine-neutral process start, option handling and search-line extraction.

UCI is the default protocol; a profile can select XBoard. Both run through
``chess.engine.Protocol``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import chess
import chess.engine

from scan64.providers.engine.config import EngineConfig, EngineConfigurationError, UciOptionValue

type Engine = chess.engine.UciProtocol | chess.engine.XBoardProtocol

# Values that leave a Stockfish-style engine at full strength. An engine that
# does not declare one of these has no such control and is already at full
# strength, so the reset is skipped for it rather than sent and rejected. A
# numeric value outside the engine's declared range is moved to the nearest
# bound. ``UCI_Elo`` is not sent: engines ignore it while ``UCI_LimitStrength``
# is off, and a placeholder can fall outside the declared range (Stockfish 11
# declares it from 1350).
FULL_STRENGTH: Mapping[str, UciOptionValue] = {
    "Skill Level": 20,
    "UCI_LimitStrength": False,
}
# Resource options: sized by Scan64, absent on some engines (Lc0 has no Hash).
RESOURCE_OPTIONS = ("Threads", "Hash")
# XBoard's equivalents: the ``cores`` and ``memory`` commands, which python-chess
# exposes as options when the engine sends ``feature smp=1`` / ``memory=1``.
XBOARD_RESOURCE_OPTIONS = ("cores", "memory")
# The bot ladder and the legacy Skill Level opponent need these controls.
LEGACY_STRENGTH_OPTIONS = ("Skill Level",)
LADDER_OPTIONS = ("MultiPV",)
# ``[ladder] strength = "elo"`` weakens the engine through these instead.
ELO_LADDER_OPTIONS = ("UCI_LimitStrength", "UCI_Elo")


class EngineCapabilityError(RuntimeError):
    """The configured engine lacks an option or protocol feature a feature requires."""

    # Tells the player which action fixes it; play sends it as ``detail.kind``.
    kind = "missing_capability"


class BotPolicyMismatchError(EngineCapabilityError):
    """A bot session was recorded under another ladder policy than the server plays."""

    kind = "policy_mismatch"


class BotStrengthRangeError(EngineCapabilityError):
    """A ladder level asks the engine for a strength outside its declared range."""

    kind = "elo_out_of_range"


# The ways one search can fail without the caller being at fault: the engine
# timed out, reported an error, exited, lacks a capability, or its process failed.
SEARCH_FAILURES: tuple[type[Exception], ...] = (
    TimeoutError,
    chess.engine.EngineError,
    EngineCapabilityError,
    OSError,
)


def search_failure_reason(error: BaseException) -> str:
    """A log-safe reason code for a ``SEARCH_FAILURES`` error: never its message."""
    if isinstance(error, chess.engine.EngineTerminatedError):
        return "engine_exited"
    if isinstance(error, TimeoutError) or isinstance(error.__cause__, TimeoutError):
        # ``bounded`` reports a search that overran its deadline as a capability error.
        return "timeout"
    if isinstance(error, EngineCapabilityError):
        return error.kind
    if isinstance(error, chess.engine.EngineError):
        return "engine_error"
    return "os_error"


def _engine_label(engine: Engine) -> str:
    return str(engine.id.get("name", "the configured engine"))


def declares(engine: Engine, name: str) -> bool:
    return name in engine.options


def missing_options(engine: Engine, names: Iterable[str]) -> list[str]:
    return [name for name in names if not declares(engine, name)]


def require_options(engine: Engine, names: Iterable[str], feature: str) -> None:
    missing = missing_options(engine, names)
    if missing:
        raise EngineCapabilityError(
            f"{feature} requires UCI option(s) {', '.join(missing)}, "
            f"which {_engine_label(engine)} does not declare"
        )


async def open_engine(config: EngineConfig) -> Engine:
    """Start the profile's engine process with its protocol.

    Every Scan64 search starts from a FEN, which XBoard sends with ``setboard``;
    an XBoard engine without that feature can serve no feature at all.
    """
    if config.protocol == "xboard":
        # popen_xboard's steps, so the declared features survive a failed start.
        transport, xboard = await chess.engine.XBoardProtocol.popen(config.binary_path)
        try:
            await xboard.initialize()
        except chess.engine.EngineError as error:
            transport.close()
            if not xboard.features.get("setboard", 0):
                raise EngineCapabilityError(
                    f"{xboard.features.get('myname', config.name)} does not declare the "
                    "XBoard setboard feature, so Scan64 cannot set up a position on it"
                ) from error
            raise
        except BaseException:
            transport.close()
            raise
        return xboard
    _, uci = await chess.engine.popen_uci(config.binary_path)
    return uci


def is_xboard(engine: Engine) -> bool:
    return isinstance(engine, chess.engine.XBoardProtocol)


def startup_options(engine: Engine, config: EngineConfig) -> dict[str, UciOptionValue]:
    """Threads/Hash (XBoard: cores/memory) where declared, and every profile option."""
    names = XBOARD_RESOURCE_OPTIONS if is_xboard(engine) else RESOURCE_OPTIONS
    resources: dict[str, UciOptionValue] = dict(
        zip(names, (config.threads, config.hash_size), strict=True)
    )
    options = {name: value for name, value in resources.items() if declares(engine, name)}
    for name, value in config.options.items():
        if not declares(engine, name):
            raise EngineConfigurationError(
                f"Engine profile {config.name!r} sets UCI option {name!r}, "
                f"which {_engine_label(engine)} does not declare"
            )
        options[name] = value
    return options


def full_strength_options(engine: Engine, threads: int) -> dict[str, UciOptionValue]:
    """Analysis reset: undo pool play's strength changes on engines that have them."""
    options: dict[str, UciOptionValue] = {}
    if declares(engine, "Threads"):
        options["Threads"] = threads
    options.update(
        {
            name: within_declared_range(engine, name, value)
            for name, value in FULL_STRENGTH.items()
            if declares(engine, name)
        }
    )
    return options


def within_declared_range(engine: Engine, name: str, value: UciOptionValue) -> UciOptionValue:
    """``value`` moved into the option's declared min/max, when it has one."""
    option = engine.options[name]
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    if not isinstance(option, chess.engine.Option):
        return value
    if option.min is not None and value < option.min:
        return option.min
    if option.max is not None and value > option.max:
        return option.max
    return value


def line_from_info(info: chess.engine.InfoDict, turn: chess.Color) -> dict[str, Any]:
    """One stored MultiPV line, scores from the side to move.

    Keys appear only when the engine reported them, so a line from an engine
    that sends no WDL or seldepth has no such key (never ``None``). ``pv``,
    ``score_cp`` and ``score_mate`` keep their pre-extension meaning.
    """
    line: dict[str, Any] = {}
    if "pv" in info:
        line["pv"] = [move.uci() for move in info["pv"]]
    if "score" in info:
        score = info["score"].pov(turn)
        if score.is_mate():
            line["score_mate"] = score.mate()
        else:
            line["score_cp"] = score.score()
    if "wdl" in info:
        wdl = info["wdl"].pov(turn)
        line["wdl"] = [wdl.wins, wdl.draws, wdl.losses]
    for key in ("depth", "seldepth", "nodes", "nps", "multipv"):
        if key in info:
            line[key] = info[key]
    return line


def lines_from_result(
    result: chess.engine.InfoDict | list[chess.engine.InfoDict],
    turn: chess.Color,
    *,
    score_scale: float,
) -> list[dict[str, Any]]:
    """Stored lines; each carries the profile's ``score_scale`` when it is not 1."""
    infos = result if isinstance(result, list) else [result]
    lines = [line_from_info(info, turn) for info in infos]
    if score_scale != 1:
        for line in lines:
            line["score_scale"] = score_scale
    return lines
