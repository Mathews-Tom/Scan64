"""Engine selection by configuration.

Without ``SCAN64_ENGINE_CONFIG`` the engine is Stockfish from
``SCAN64_STOCKFISH_PATH`` (default ``stockfish`` on ``PATH``), one thread and a
16 MB hash: exactly the pre-configuration behaviour. With it, a TOML file names
any UCI engine, its extra UCI options and its per-use search limits.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from scan64.chess.opponents.bots import BOT_LEVELS

ENGINE_CONFIG_ENV = "SCAN64_ENGINE_CONFIG"
STOCKFISH_PATH_ENV = "SCAN64_STOCKFISH_PATH"
DEFAULT_ENGINE_NAME = "stockfish"
DEFAULT_BINARY = "stockfish"
DEFAULT_THREADS = 1
DEFAULT_HASH_MB = 16
# Scan64's node budgets are sized for an alpha-beta engine's nodes/second. An
# engine with slow nodes (Lc0, Maia-3) sets ``engine.node_scale`` below 1.
DEFAULT_NODE_SCALE = 1.0
# Scan64's centipawn policy (the Lichess win-probability curve) is fitted to
# Stockfish. ``engine.score_scale`` is this engine's centipawns per Stockfish
# centipawn (Arasan 26.1 about 2.8); win probability divides by it.
DEFAULT_SCORE_SCALE = 1.0
# Wire protocol of the engine process. XBoard engines get single-line analysis
# only: python-chess rejects MultiPV over XBoard.
type EngineProtocolName = Literal["uci", "xboard"]
PROTOCOLS: tuple[EngineProtocolName, ...] = ("uci", "xboard")
DEFAULT_PROTOCOL: EngineProtocolName = "uci"

# python-chess manages these per search; the strength controls are reset by the
# pool before every analysis. A profile must not pin either group globally.
RESERVED_OPTIONS = frozenset(
    {
        "Threads",
        "Hash",
        "MultiPV",
        "Ponder",
        "UCI_AnalyseMode",
        "UCI_Chess960",
        "UCI_Variant",
        "UCI_Opponent",
        "UCI_RatingAdv",
        "UCI_Elo",
        "UCI_LimitStrength",
        "Skill Level",
        # XBoard resources, sized from engine.threads / engine.hash_mb.
        "cores",
        "memory",
    }
)

type UciOptionValue = bool | int | str


class EngineConfigurationError(RuntimeError):
    """Raised when the selected engine configuration is unusable."""


@dataclass(frozen=True)
class SearchLimits:
    nodes: int | None = None
    time_ms: int | None = None
    multipv: int = 1

    def __post_init__(self) -> None:
        if self.nodes is None and self.time_ms is None:
            raise EngineConfigurationError("A search limit needs nodes or time_ms")
        for name in ("nodes", "time_ms"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 1):
                raise EngineConfigurationError(f"limit {name} must be a positive integer")
        if isinstance(self.multipv, bool) or self.multipv < 1:
            raise EngineConfigurationError("limit multipv must be a positive integer")


# How the bot ladder weakens the engine. ``skill``: full-strength MultiPV over
# every legal move, then Scan64's seeded selection (needs Stockfish's
# ``Skill Level`` reset and MultiPV). ``elo``: the engine's own
# ``UCI_LimitStrength``/``UCI_Elo``, one ``UCI_Elo`` per level and a fixed move
# time, for engines without ``Skill Level`` or that ignore ``go nodes`` (Arasan).
type LadderStrength = Literal["skill", "elo"]
LADDER_STRENGTHS: tuple[LadderStrength, ...] = ("skill", "elo")
DEFAULT_LADDER_STRENGTH: LadderStrength = "skill"


@dataclass(frozen=True)
class LadderSettings:
    strength: LadderStrength = DEFAULT_LADDER_STRENGTH
    # ``UCI_Elo`` for levels 1..8, in level order; ``elo`` strength only.
    elo: tuple[int, ...] = ()
    movetime_ms: int | None = None

    def elo_for(self, level: int) -> int:
        if self.strength != "elo":
            raise EngineConfigurationError("The ladder has no UCI_Elo per level under skill")
        return self.elo[level - 1]


def _ladder(raw: object, config_path: Path) -> LadderSettings:
    if raw is None:
        return LadderSettings()
    if not isinstance(raw, dict):
        raise EngineConfigurationError(f"{config_path}: [ladder] must be a table")
    _reject_unknown(raw, {"strength", "elo", "movetime_ms"}, "ladder")
    strength = raw.get("strength", DEFAULT_LADDER_STRENGTH)
    if strength == "skill":
        extra = sorted(set(raw) & {"elo", "movetime_ms"})
        if extra:
            raise EngineConfigurationError(
                f'{config_path}: ladder.{extra[0]} applies only to ladder.strength = "elo"'
            )
        return LadderSettings()
    if strength != "elo":
        raise EngineConfigurationError(
            f"{config_path}: ladder.strength must be one of {', '.join(LADDER_STRENGTHS)}"
        )
    elo = raw.get("elo")
    if (
        not isinstance(elo, list)
        or len(elo) != len(BOT_LEVELS)
        or any(isinstance(value, bool) or not isinstance(value, int) for value in elo)
    ):
        raise EngineConfigurationError(
            f"{config_path}: ladder.elo must list {len(BOT_LEVELS)} integers, one per level"
        )
    if any(low >= high for low, high in zip(elo, elo[1:], strict=False)):
        raise EngineConfigurationError(f"{config_path}: ladder.elo must increase with the level")
    return LadderSettings(
        strength="elo",
        elo=tuple(elo),
        movetime_ms=_positive(raw, "movetime_ms", 0, f"{config_path}: ladder"),
    )


# Uses whose search limits a profile may override. Absent uses keep the
# caller's built-in budget (for ``hint``: ``scan64.providers.stockfish.pool``).
KNOWN_USES = frozenset({"hint"})


@dataclass(frozen=True)
class EngineProfile:
    """One parsed engine selection; ``EngineConfig`` defaults come from here."""

    name: str
    binary_path: str
    protocol: EngineProtocolName
    threads: int
    hash_size: int
    node_scale: float
    score_scale: float
    options: MappingProxyType[str, UciOptionValue]
    limits: MappingProxyType[str, SearchLimits]
    ladder: LadderSettings
    source: Path | None


def _stockfish_profile() -> EngineProfile:
    return EngineProfile(
        name=DEFAULT_ENGINE_NAME,
        binary_path=os.environ.get(STOCKFISH_PATH_ENV, DEFAULT_BINARY),
        protocol=DEFAULT_PROTOCOL,
        threads=DEFAULT_THREADS,
        hash_size=DEFAULT_HASH_MB,
        node_scale=DEFAULT_NODE_SCALE,
        score_scale=DEFAULT_SCORE_SCALE,
        options=MappingProxyType({}),
        limits=MappingProxyType({}),
        ladder=LadderSettings(),
        source=None,
    )


def _positive(table: dict[str, Any], key: str, default: int, where: str) -> int:
    value = table.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EngineConfigurationError(f"{where}.{key} must be a positive integer")
    return value


def _protocol(table: dict[str, Any], where: str) -> EngineProtocolName:
    value = table.get("protocol", DEFAULT_PROTOCOL)
    for protocol in PROTOCOLS:
        if value == protocol:
            return protocol
    raise EngineConfigurationError(f"{where}.protocol must be one of {', '.join(PROTOCOLS)}")


def _node_scale(table: dict[str, Any], where: str) -> float:
    value = table.get("node_scale", DEFAULT_NODE_SCALE)
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 1:
        raise EngineConfigurationError(f"{where}.node_scale must be a number in (0, 1]")
    return float(value)


def _score_scale(table: dict[str, Any], where: str) -> float:
    value = table.get("score_scale", DEFAULT_SCORE_SCALE)
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise EngineConfigurationError(f"{where}.score_scale must be a positive number")
    return float(value)


def scaled_nodes(nodes: int | None, node_scale: float) -> int | None:
    """The node count sent to the engine for a Scan64 budget of ``nodes``."""
    if nodes is None:
        return None
    return max(1, round(nodes * node_scale))


def _reject_unknown(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise EngineConfigurationError(f"Unknown keys in [{where}]: {', '.join(unknown)}")


def _binary_path(raw: object, config_path: Path) -> str:
    if not isinstance(raw, str) or not raw:
        raise EngineConfigurationError(f"{config_path}: engine.binary_path is required")
    expanded = os.path.expanduser(raw)
    if os.sep in expanded or (os.altsep is not None and os.altsep in expanded):
        if not Path(expanded).is_absolute():
            raise EngineConfigurationError(
                f"{config_path}: engine.binary_path must be absolute or a bare command name"
            )
    return expanded


def _limits(raw: object, config_path: Path) -> MappingProxyType[str, SearchLimits]:
    limits: dict[str, SearchLimits] = {}
    if raw is None:
        return MappingProxyType(limits)
    if not isinstance(raw, dict):
        raise EngineConfigurationError(f"{config_path}: [limits] must be a table")
    _reject_unknown(raw, set(KNOWN_USES), "limits")
    for use, table in raw.items():
        if not isinstance(table, dict):
            raise EngineConfigurationError(f"{config_path}: [limits.{use}] must be a table")
        # Hints run under a wall-clock deadline: nodes and/or time, never depth.
        _reject_unknown(table, {"nodes", "time_ms", "multipv"}, f"limits.{use}")
        limits[use] = SearchLimits(
            nodes=table.get("nodes"),
            time_ms=table.get("time_ms"),
            multipv=table.get("multipv", 1),
        )
    return MappingProxyType(limits)


def load_engine_profile(path: Path) -> EngineProfile:
    """Parse one engine TOML file; every error names the file and the key."""
    if not path.is_absolute():
        raise EngineConfigurationError(f"{ENGINE_CONFIG_ENV} must be an absolute path: {path}")
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError as error:
        raise EngineConfigurationError(f"Engine configuration does not exist: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise EngineConfigurationError(f"Invalid engine configuration {path}: {error}") from error
    _reject_unknown(raw, {"engine", "limits", "ladder"}, "root")
    engine = raw.get("engine")
    if not isinstance(engine, dict):
        raise EngineConfigurationError(f"{path}: an [engine] table is required")
    _reject_unknown(
        engine,
        {
            "name",
            "binary_path",
            "protocol",
            "threads",
            "hash_mb",
            "node_scale",
            "score_scale",
            "options",
        },
        "engine",
    )
    name = engine.get("name")
    if not isinstance(name, str) or not name.strip():
        raise EngineConfigurationError(f"{path}: engine.name is required")
    options = engine.get("options", {})
    if not isinstance(options, dict):
        raise EngineConfigurationError(f"{path}: [engine.options] must be a table")
    for option, value in options.items():
        if option in RESERVED_OPTIONS:
            raise EngineConfigurationError(
                f"{path}: engine.options.{option} is managed by Scan64; "
                "use engine.threads / engine.hash_mb or the per-use limits"
            )
        if not isinstance(value, bool | int | str):
            raise EngineConfigurationError(
                f"{path}: engine.options.{option} must be a boolean, integer or string"
            )
    return EngineProfile(
        name=name.strip(),
        binary_path=_binary_path(engine.get("binary_path"), path),
        protocol=_protocol(engine, f"{path}: engine"),
        threads=_positive(engine, "threads", DEFAULT_THREADS, "engine"),
        hash_size=_positive(engine, "hash_mb", DEFAULT_HASH_MB, "engine"),
        node_scale=_node_scale(engine, f"{path}: engine"),
        score_scale=_score_scale(engine, f"{path}: engine"),
        options=MappingProxyType(dict(options)),
        limits=_limits(raw.get("limits"), path),
        ladder=_ladder(raw.get("ladder"), path),
        source=path,
    )


_cache: tuple[tuple[object, ...], EngineProfile] | None = None


def active_engine_profile() -> EngineProfile:
    """The profile selected by the environment, re-read when the file changes."""
    global _cache
    raw_path = os.environ.get(ENGINE_CONFIG_ENV)
    if raw_path is None or raw_path == "":
        return _stockfish_profile()
    path = Path(raw_path)
    try:
        stat = path.stat()
    except FileNotFoundError as error:
        raise EngineConfigurationError(f"Engine configuration does not exist: {path}") from error
    key = (raw_path, stat.st_mtime_ns, stat.st_size)
    if _cache is None or _cache[0] != key:
        _cache = (key, load_engine_profile(path))
    return _cache[1]


@dataclass
class EngineConfig:
    """Effective engine inputs. Unset fields come from ``active_engine_profile``.

    ``StockfishConfig`` is this class; positional and keyword construction
    (``binary_path``, ``threads``, ``hash_size``) is unchanged.
    """

    binary_path: str = field(default_factory=lambda: active_engine_profile().binary_path)
    threads: int = field(default_factory=lambda: active_engine_profile().threads)
    hash_size: int = field(default_factory=lambda: active_engine_profile().hash_size)
    name: str = field(default_factory=lambda: active_engine_profile().name)
    node_scale: float = field(default_factory=lambda: active_engine_profile().node_scale)
    score_scale: float = field(default_factory=lambda: active_engine_profile().score_scale)
    protocol: EngineProtocolName = field(default_factory=lambda: active_engine_profile().protocol)
    options: Mapping[str, UciOptionValue] = field(
        default_factory=lambda: active_engine_profile().options
    )
    limits: Mapping[str, SearchLimits] = field(
        default_factory=lambda: active_engine_profile().limits
    )
    # Bot play only: not part of ``fingerprint``, which keys analysis results.
    ladder: LadderSettings = field(default_factory=lambda: active_engine_profile().ladder)

    def fingerprint(self) -> tuple[object, ...]:
        """Process-local identity of every input that can change a search result."""
        return (
            self.name,
            self.binary_path,
            self.protocol,
            self.threads,
            self.hash_size,
            self.node_scale,
            self.score_scale,
            tuple(sorted(self.options.items())),
        )
