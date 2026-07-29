import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import chess
import chess.engine

from scan64.chess.analysis.models import EngineAnalysis
from scan64.providers.stockfish.adapter import StockfishConfig

DEFAULT_INTERACTIVE_CONCURRENCY = 2
DEFAULT_BATCH_CONCURRENCY = 2


def _positive_int_from_env(env_var: str, default: int) -> int:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{env_var} must be an integer, got {raw_value!r}") from error
    if value < 1:
        raise ValueError(f"{env_var} must be at least 1")
    return value


def interactive_concurrency() -> int:
    """Interactive-pool engine count. ``SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY``
    overrides the default of ``2``; live opponent moves and on-demand review draw
    from this pool and must never wait behind batch work."""
    return _positive_int_from_env(
        "SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY", DEFAULT_INTERACTIVE_CONCURRENCY
    )


def batch_concurrency() -> int:
    """Batch-pool engine count. ``SCAN64_ENGINE_POOL_BATCH_CONCURRENCY`` overrides
    the default of ``2``; bulk imports and analysis jobs draw from this pool."""
    return _positive_int_from_env("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", DEFAULT_BATCH_CONCURRENCY)


def engine_pool_enabled() -> bool:
    """Whether the production path should route engine work through pooled,
    lifespan-bound processes. ``SCAN64_ENGINE_POOL_ENABLED=0`` (or ``false``)
    reverts to constructing a fresh engine process per call, the pre-M41
    behaviour, retained for one release as a rollback valve."""
    raw_value = os.environ.get("SCAN64_ENGINE_POOL_ENABLED")
    if raw_value is None:
        return True
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}


class PooledStockfishAdapter:
    def __init__(self, config: StockfishConfig):
        self.config = config
        self._engine: chess.engine.UciProtocol | None = None
        self.game_token: object | None = None

    async def ensure_started(self) -> None:
        if self._engine is None:
            _, self._engine = await chess.engine.popen_uci(self.config.binary_path)
            await self._engine.configure(
                {"Threads": self.config.threads, "Hash": self.config.hash_size}
            )

    async def reset(self) -> None:
        """Force the next UCI call to present a fresh game identity.

        ``chess.engine`` only sends ``ucinewgame`` when the ``game`` object
        passed to ``analyse``/``play`` differs from the one recorded on the
        prior call. A pooled adapter is reused across unrelated analyses and
        play sessions, so without this every checkout after the first would
        silently skip ``ucinewgame`` and could leak transposition-table and
        search-history state between them.
        """
        self.game_token = object()

    async def quit(self) -> None:
        if self._engine is not None:
            await self._engine.quit()
            self._engine = None

    async def play_move(
        self, fen: str, skill_level: int, limit: chess.engine.Limit
    ) -> chess.engine.PlayResult:
        await self.ensure_started()
        assert self._engine is not None
        await self._engine.configure({"Skill Level": skill_level})
        board = chess.Board(fen)
        return await self._engine.play(
            board, limit, info=chess.engine.INFO_SCORE, game=self.game_token
        )

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        await self.ensure_started()
        assert self._engine is not None
        engine = self._engine
        board = chess.Board(fen)
        limit = chess.engine.Limit(
            nodes=nodes, depth=depth, time=time_ms / 1000.0 if time_ms else None
        )

        info_result = await engine.analyse(board, limit, multipv=multipv, game=self.game_token)

        if not isinstance(info_result, list):
            info_result = [info_result]

        raw_result = []
        for info in info_result:
            res: dict[str, Any] = {}
            if "pv" in info:
                res["pv"] = [m.uci() for m in info["pv"]]
            if "score" in info:
                score = info["score"].pov(board.turn)
                if score.is_mate():
                    res["score_mate"] = score.mate()
                else:
                    res["score_cp"] = score.score()
            raw_result.append(res)

        engine_name = engine.id.get("name", "Stockfish")

        config_dict = {
            "engine_name": engine_name,
            "engine_version": "unknown",
            "nodes": nodes,
            "depth": depth,
            "time_ms": time_ms,
            "multipv": multipv,
        }

        return EngineAnalysis(position_id=None, config=config_dict, raw_result=raw_result)


class EnginePool:
    def __init__(self, config: StockfishConfig, concurrency: int):
        self.config = config
        self.concurrency = concurrency
        self._queue: asyncio.Queue[PooledStockfishAdapter] = asyncio.Queue()
        self._initialized = False
        self.closed = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        for _ in range(self.concurrency):
            adapter = PooledStockfishAdapter(self.config)
            await adapter.ensure_started()
            self._queue.put_nowait(adapter)
        self._initialized = True

    async def close(self) -> None:
        while not self._queue.empty():
            adapter = self._queue.get_nowait()
            await adapter.quit()
        self.closed = True

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[PooledStockfishAdapter, None]:
        await self.initialize()
        adapter = await self._queue.get()
        await adapter.reset()
        try:
            yield adapter
        finally:
            self._queue.put_nowait(adapter)


class PoolBoundAdapter:
    """``EngineAdapter``-shaped facade that checks an engine out of an
    ``EnginePool`` for the duration of a single ``analyze_position`` call.
    Lets `FastPassOrchestrator`/`FocusedPassOrchestrator` route through a
    pool without knowing pools exist."""

    def __init__(self, pool: EnginePool):
        self._pool = pool

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        async with self._pool.acquire() as adapter:
            return await adapter.analyze_position(
                fen, nodes=nodes, depth=depth, time_ms=time_ms, multipv=multipv
            )


class EnginePoolManager:
    def __init__(
        self, config: StockfishConfig, interactive_concurrency: int = 2, batch_concurrency: int = 2
    ):
        self.config = config
        self.interactive_pool = EnginePool(config, interactive_concurrency)
        self.batch_pool = EnginePool(config, batch_concurrency)

    @classmethod
    def from_env(cls, config: StockfishConfig) -> "EnginePoolManager":
        """Build a manager sized from ``SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY``
        and ``SCAN64_ENGINE_POOL_BATCH_CONCURRENCY`` (both default ``2``). This is
        the constructor the FastAPI lifespan uses; direct callers (tests, the
        CLI) that want explicit concurrency keep using ``__init__``."""
        return cls(
            config,
            interactive_concurrency=interactive_concurrency(),
            batch_concurrency=batch_concurrency(),
        )

    @property
    def closed(self) -> bool:
        return self.interactive_pool.closed and self.batch_pool.closed

    @property
    def batch_adapter(self) -> PoolBoundAdapter:
        """`EngineAdapter`-shaped view of the batch pool, for
        `FastPassOrchestrator`/`FocusedPassOrchestrator`."""
        return PoolBoundAdapter(self.batch_pool)

    async def close(self) -> None:
        await self.interactive_pool.close()
        await self.batch_pool.close()

    async def analyze_interactive(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        multipv: int = 1,
        time_ms: int | None = None,
    ) -> EngineAnalysis:
        async with self.interactive_pool.acquire() as adapter:
            return await adapter.analyze_position(
                fen, nodes=nodes, depth=depth, multipv=multipv, time_ms=time_ms
            )

    async def play_interactive(
        self, fen: str, skill_level: int, limit: chess.engine.Limit
    ) -> chess.engine.PlayResult:
        async with self.interactive_pool.acquire() as adapter:
            return await adapter.play_move(fen, skill_level, limit)

    async def analyze_batch(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        multipv: int = 1,
        time_ms: int | None = None,
    ) -> EngineAnalysis:
        async with self.batch_pool.acquire() as adapter:
            return await adapter.analyze_position(
                fen, nodes=nodes, depth=depth, multipv=multipv, time_ms=time_ms
            )
