"""Per-call UCI engine adapter. Stockfish is the default engine, not a requirement.

``StockfishConfig``/``StockfishAdapter`` are kept as the historical names of
``EngineConfig``/``UciEngineAdapter``; see ``scan64.providers.engine``.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, Protocol, runtime_checkable

import chess
import chess.engine

from scan64.chess.analysis.models import EngineAnalysis
from scan64.providers.engine.config import EngineConfig, scaled_nodes
from scan64.providers.engine.uci import (
    Engine,
    EngineCapabilityError,
    declares,
    is_xboard,
    lines_from_result,
    open_engine,
    startup_options,
)
from scan64.providers.stockfish.identity import (
    ExecutableSignature,
    analysis_identity,
    engine_identity,
    executable_signature,
)


@runtime_checkable
class EngineAdapter(Protocol):
    """Duck-typed engine-analysis interface shared by the per-call
    ``StockfishAdapter`` and the pool-backed adapter used once M41's batch
    pool is enabled. ``FastPassOrchestrator``/``FocusedPassOrchestrator``
    depend on this, not on a concrete adapter class."""

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis: ...


@runtime_checkable
class IdentifiedEngineAdapter(EngineAdapter, Protocol):
    async def analysis_identity(self) -> dict[str, Any]: ...


StockfishConfig = EngineConfig


def search_limit(
    nodes: int | None, depth: int | None, time_ms: int | None, *, node_scale: float
) -> chess.engine.Limit:
    """``nodes`` is a Scan64 budget; the engine receives it times ``node_scale``."""
    return chess.engine.Limit(
        nodes=scaled_nodes(nodes, node_scale),
        depth=depth,
        time=time_ms / 1000.0 if time_ms else None,
    )


async def search(
    engine: Engine,
    board: chess.Board,
    limit: chess.engine.Limit,
    multipv: int,
    game: object | None = None,
    *,
    deadline_s: float,
) -> list[dict[str, Any]]:
    """Run one search and return every MultiPV line in stored form.

    python-chess raises ``EngineError`` for ``multipv > 1`` on a UCI engine that
    does not declare ``MultiPV``, and for any ``multipv`` argument over XBoard.
    An XBoard engine therefore gets a single-line search with no ``multipv``
    argument. A request for more lines than either engine can give is a
    capability error; no single-line substitute is ever returned for a
    multi-line request.

    A search without a time limit must finish within ``deadline_s`` seconds;
    otherwise it is stopped and the engine is reported as not honouring its
    node or depth limit.
    """
    if is_xboard(engine):
        if multipv > 1:
            raise EngineCapabilityError(
                f"{multipv} analysis lines need MultiPV, which "
                f"{engine.id.get('name', 'the configured engine')} cannot provide over XBoard"
            )
        single = await bounded(engine, engine.analyse(board, limit, game=game), limit, deadline_s)
        return lines_from_result(single, board.turn)
    if multipv > 1 and not declares(engine, "MultiPV"):
        raise EngineCapabilityError(
            f"{multipv} analysis lines need the UCI option MultiPV, which "
            f"{engine.id.get('name', 'the configured engine')} does not declare"
        )
    result = await bounded(
        engine, engine.analyse(board, limit, multipv=multipv, game=game), limit, deadline_s
    )
    return lines_from_result(result, board.turn)


async def bounded[T](
    engine: Engine, search: Coroutine[Any, Any, T], limit: chess.engine.Limit, deadline_s: float
) -> T:
    """``search`` under ``deadline_s`` when ``limit`` sets no time of its own."""
    if limit.time is not None:
        return await search
    try:
        return await asyncio.wait_for(search, deadline_s)
    except TimeoutError as error:
        bound = f"{limit.nodes} nodes" if limit.nodes is not None else f"depth {limit.depth}"
        raise EngineCapabilityError(
            f"{engine.id.get('name', 'the configured engine')} did not finish a search limited "
            f"to {bound} within {deadline_s:g} s; Scan64 sizes its searches by node budget, "
            "so the engine must honour go nodes"
        ) from error


class StockfishAdapter:
    def __init__(self, config: EngineConfig):
        self.config = config
        self._identity: dict[str, Any] | None = None
        self._identity_signature: ExecutableSignature | None = None

    async def _start(self) -> Engine:
        engine = await open_engine(self.config)
        try:
            await engine.configure(startup_options(engine, self.config))
        except BaseException:
            await engine.quit()
            raise
        return engine

    def _effective_identity(self) -> dict[str, Any]:
        assert self._identity is not None
        identity = dict(self._identity)
        resources = {"Threads": self.config.threads, "Hash": self.config.hash_size}
        identity["options"] = dict(
            identity["options"],
            **{name: value for name, value in resources.items() if name in identity["options"]},
        )
        return identity

    async def analysis_identity(self) -> dict[str, Any]:
        # Unlike a live pooled process, a per-call adapter has no loaded build
        # between calls. A replaced executable must invalidate its probe.
        signature = executable_signature(self.config.binary_path)
        if self._identity is None or self._identity_signature != signature:
            engine = await self._start()
            try:
                self._identity = analysis_identity(
                    engine,
                    engine_identity(engine, self.config.binary_path),
                    self.config.threads,
                    self.config.node_scale,
                )
                self._identity_signature = signature
            finally:
                await engine.quit()
        return self._effective_identity()

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        signature = executable_signature(self.config.binary_path)
        engine = await self._start()
        try:
            self._identity = analysis_identity(
                engine,
                engine_identity(engine, self.config.binary_path),
                self.config.threads,
                self.config.node_scale,
            )
            self._identity_signature = signature
            limit = search_limit(nodes, depth, time_ms, node_scale=self.config.node_scale)
            raw_result = await search(
                engine, chess.Board(fen), limit, multipv, deadline_s=self.config.search_deadline_s
            )
            config_dict = dict(
                self._identity, nodes=nodes, depth=depth, time_ms=time_ms, multipv=multipv
            )
            return EngineAnalysis(
                position_id=None,  # To be filled by the caller
                config=config_dict,
                raw_result=raw_result,
            )
        finally:
            await engine.quit()


UciEngineAdapter = StockfishAdapter

__all__ = [
    "EngineAdapter",
    "IdentifiedEngineAdapter",
    "StockfishAdapter",
    "StockfishConfig",
    "UciEngineAdapter",
    "search",
    "search_limit",
]
