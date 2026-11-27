"""Engine selection by configuration.

Without ``SCAN64_ENGINE_CONFIG`` the engine is Stockfish from
``SCAN64_STOCKFISH_PATH`` (default ``stockfish`` on ``PATH``), one thread and a
16 MB hash: exactly the pre-configuration behaviour. With it, a TOML file names
any UCI engine, its extra UCI options and its per-use search limits.
"""

from __future__ import annotations

import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

ENGINE_CONFIG_ENV = "SCAN64_ENGINE_CONFIG"
STOCKFISH_PATH_ENV = "SCAN64_STOCKFISH_PATH"
DEFAULT_ENGINE_NAME = "stockfish"
DEFAULT_BINARY = "stockfish"
DEFAULT_THREADS = 1
DEFAULT_HASH_MB = 16
# Scan64's node budgets are sized for an alpha-beta engine's nodes/second. An
# engine with slow nodes (Lc0, Maia-3) sets ``engine.node_scale`` below 1.
DEFAULT_NODE_SCALE = 1.0
# Wall-clock bound on a search limited by nodes or depth only. Such a search
# has no time limit of its own, so an engine that ignores ``go nodes`` (Rodent
# V 1.2) would run until killed. Stockfish's largest Scan64 search (1M nodes)
# takes about a second.
DEFAULT_SEARCH_DEADLINE_S = 120.0
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
    search_deadline_s: float
    options: MappingProxyType[str, UciOptionValue]
    limits: MappingProxyType[str, SearchLimits]
    source: Path | None


def _stockfish_profile() -> EngineProfile:
    return EngineProfile(
        name=DEFAULT_ENGINE_NAME,
        binary_path=os.environ.get(STOCKFISH_PATH_ENV, DEFAULT_BINARY),
        protocol=DEFAULT_PROTOCOL,
        threads=DEFAULT_THREADS,
        hash_size=DEFAULT_HASH_MB,
        node_scale=DEFAULT_NODE_SCALE,
        search_deadline_s=DEFAULT_SEARCH_DEADLINE_S,
        options=MappingProxyType({}),
        limits=MappingProxyType({}),
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


def _search_deadline(table: dict[str, Any], where: str) -> float:
    value = table.get("search_deadline_s", DEFAULT_SEARCH_DEADLINE_S)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not value > 0
    ):
        # TOML accepts ``inf``, which would remove the bound.
        raise EngineConfigurationError(
            f"{where}.search_deadline_s must be a positive finite number"
        )
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
    _reject_unknown(raw, {"engine", "limits"}, "root")
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
            "search_deadline_s",
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
        search_deadline_s=_search_deadline(engine, f"{path}: engine"),
        options=MappingProxyType(dict(options)),
        limits=_limits(raw.get("limits"), path),
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


@dataclass(init=False)
class EngineConfig:
    """Effective engine inputs. Unset fields come from ``active_engine_profile``.

    ``StockfishConfig`` is this class; positional and keyword construction
    (``binary_path``, ``threads``, ``hash_size``) is unchanged. An explicit
    ``binary_path`` names a Stockfish binary: unless given, ``name`` is
    ``stockfish``, ``options`` and ``limits`` are empty, ``protocol`` is UCI and
    ``node_scale`` and ``search_deadline_s`` are their defaults, so a
    non-Stockfish profile does not leak its identity, protocol, node scale or
    deadline onto that binary (``scan64 calibrate --stockfish``).
    """

    binary_path: str
    threads: int
    hash_size: int
    name: str
    node_scale: float
    protocol: EngineProtocolName
    search_deadline_s: float
    options: Mapping[str, UciOptionValue]
    limits: Mapping[str, SearchLimits]

    def __init__(
        self,
        binary_path: str | None = None,
        threads: int | None = None,
        hash_size: int | None = None,
        name: str | None = None,
        options: Mapping[str, UciOptionValue] | None = None,
        limits: Mapping[str, SearchLimits] | None = None,
        node_scale: float | None = None,
        protocol: EngineProtocolName | None = None,
        search_deadline_s: float | None = None,
    ) -> None:
        explicit_binary = binary_path is not None
        self.binary_path = (
            binary_path if binary_path is not None else active_engine_profile().binary_path
        )
        self.threads = threads if threads is not None else active_engine_profile().threads
        self.hash_size = hash_size if hash_size is not None else active_engine_profile().hash_size
        if name is not None:
            self.name = name
        else:
            self.name = DEFAULT_ENGINE_NAME if explicit_binary else active_engine_profile().name
        if options is not None:
            self.options = options
        else:
            self.options = (
                MappingProxyType({}) if explicit_binary else active_engine_profile().options
            )
        if limits is not None:
            self.limits = limits
        else:
            self.limits = (
                MappingProxyType({}) if explicit_binary else active_engine_profile().limits
            )
        if node_scale is not None:
            self.node_scale = node_scale
        else:
            self.node_scale = (
                DEFAULT_NODE_SCALE if explicit_binary else active_engine_profile().node_scale
            )
        if protocol is not None:
            self.protocol = protocol
        else:
            self.protocol = (
                DEFAULT_PROTOCOL if explicit_binary else active_engine_profile().protocol
            )
        if search_deadline_s is not None:
            self.search_deadline_s = search_deadline_s
        else:
            self.search_deadline_s = (
                DEFAULT_SEARCH_DEADLINE_S
                if explicit_binary
                else active_engine_profile().search_deadline_s
            )

    def fingerprint(self) -> tuple[object, ...]:
        """Process-local identity of every input that can change a search result."""
        return (
            self.name,
            self.binary_path,
            self.protocol,
            self.threads,
            self.hash_size,
            self.node_scale,
            tuple(sorted(self.options.items())),
        )
