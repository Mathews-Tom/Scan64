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
# strength, so the reset is skipped for it rather than sent and rejected.
FULL_STRENGTH: Mapping[str, UciOptionValue] = {
    "Skill Level": 20,
    "UCI_LimitStrength": False,
    "UCI_Elo": 1320,
}
# Resource options: sized by Scan64, absent on some engines (Lc0 has no Hash).
RESOURCE_OPTIONS = ("Threads", "Hash")
# XBoard's equivalents: the ``cores`` and ``memory`` commands, which python-chess
# exposes as options when the engine sends ``feature smp=1`` / ``memory=1``.
XBOARD_RESOURCE_OPTIONS = ("cores", "memory")
# The bot ladder and the legacy Skill Level opponent need these controls.
LEGACY_STRENGTH_OPTIONS = ("Skill Level",)
LADDER_OPTIONS = ("MultiPV",)


class EngineCapabilityError(RuntimeError):
    """The configured engine lacks an option or protocol feature a feature requires."""


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
    """Start the profile's engine process with its protocol."""
    if config.protocol == "xboard":
        _, xboard = await chess.engine.popen_xboard(config.binary_path)
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
    options.update({name: value for name, value in FULL_STRENGTH.items() if declares(engine, name)})
    return options


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
    result: chess.engine.InfoDict | list[chess.engine.InfoDict], turn: chess.Color
) -> list[dict[str, Any]]:
    infos = result if isinstance(result, list) else [result]
    return [line_from_info(info, turn) for info in infos]
