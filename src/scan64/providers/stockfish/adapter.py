"""Per-call UCI engine adapter. Stockfish is the default engine, not a requirement.

``StockfishConfig``/``StockfishAdapter`` are kept as the historical names of
``EngineConfig``/``UciEngineAdapter``; see ``scan64.providers.engine``.
"""

from typing import Any, Protocol, runtime_checkable

import chess
import chess.engine

from scan64.chess.analysis.models import EngineAnalysis
from scan64.providers.engine.config import EngineConfig
from scan64.providers.engine.uci import lines_from_result, startup_options
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


def search_limit(nodes: int | None, depth: int | None, time_ms: int | None) -> chess.engine.Limit:
    return chess.engine.Limit(nodes=nodes, depth=depth, time=time_ms / 1000.0 if time_ms else None)


async def search(
    engine: chess.engine.UciProtocol,
    board: chess.Board,
    limit: chess.engine.Limit,
    multipv: int,
    game: object | None = None,
) -> list[dict[str, Any]]:
    """Run one search and return every MultiPV line in stored form.

    python-chess raises ``EngineError`` for ``multipv > 1`` on an engine that
    does not declare ``MultiPV``; no single-line substitute is attempted.
    """
    result = await engine.analyse(board, limit, multipv=multipv, game=game)
    return lines_from_result(result, board.turn)


class StockfishAdapter:
    def __init__(self, config: EngineConfig):
        self.config = config
        self._identity: dict[str, Any] | None = None
        self._identity_signature: ExecutableSignature | None = None

    async def _start(self) -> chess.engine.UciProtocol:
        _, engine = await chess.engine.popen_uci(self.config.binary_path)
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
                    engine, engine_identity(engine, self.config.binary_path), self.config.threads
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
                engine, engine_identity(engine, self.config.binary_path), self.config.threads
            )
            self._identity_signature = signature
            raw_result = await search(
                engine, chess.Board(fen), search_limit(nodes, depth, time_ms), multipv
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
