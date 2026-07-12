import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import chess
import chess.engine

from src.scan64.chess.analysis.models import EngineAnalysis
from src.scan64.providers.stockfish.adapter import StockfishConfig


class PooledStockfishAdapter:
    def __init__(self, config: StockfishConfig):
        self.config = config
        self._engine = None

    async def ensure_started(self):
        if self._engine is None:
            _, self._engine = await chess.engine.popen_uci(self.config.binary_path)
            await self._engine.configure(
                {"Threads": self.config.threads, "Hash": self.config.hash_size}
            )

    async def quit(self):
        if self._engine is not None:
            await self._engine.quit()
            self._engine = None

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        await self.ensure_started()
        board = chess.Board(fen)
        limit = chess.engine.Limit(
            nodes=nodes, depth=depth, time=time_ms / 1000.0 if time_ms else None
        )

        info_result = await self._engine.analyse(board, limit, multipv=multipv)

        if not isinstance(info_result, list):
            info_result = [info_result]

        raw_result = []
        for info in info_result:
            res = {}
            if "pv" in info:
                res["pv"] = [m.uci() for m in info["pv"]]
            if "score" in info:
                score = info["score"].pov(board.turn)
                if score.is_mate():
                    res["score_mate"] = score.mate()
                else:
                    res["score_cp"] = score.score()
            raw_result.append(res)

        engine_name = self._engine.id.get("name", "Stockfish")

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

    async def initialize(self):
        if self._initialized:
            return
        for _ in range(self.concurrency):
            adapter = PooledStockfishAdapter(self.config)
            await adapter.ensure_started()
            self._queue.put_nowait(adapter)
        self._initialized = True

    async def close(self):
        while not self._queue.empty():
            adapter = self._queue.get_nowait()
            await adapter.quit()

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[PooledStockfishAdapter, None]:
        await self.initialize()
        adapter = await self._queue.get()
        try:
            yield adapter
        finally:
            self._queue.put_nowait(adapter)


class EnginePoolManager:
    def __init__(
        self, config: StockfishConfig, interactive_concurrency: int = 2, batch_concurrency: int = 2
    ):
        self.config = config
        self.interactive_pool = EnginePool(config, interactive_concurrency)
        self.batch_pool = EnginePool(config, batch_concurrency)

    async def close(self):
        await self.interactive_pool.close()
        await self.batch_pool.close()

    async def analyze_interactive(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        multipv: int = 1,
        time_ms: int | None = None,
    ):
        async with self.interactive_pool.acquire() as adapter:
            return await adapter.analyze_position(
                fen, nodes=nodes, depth=depth, multipv=multipv, time_ms=time_ms
            )

    async def analyze_batch(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        multipv: int = 1,
        time_ms: int | None = None,
    ):
        async with self.batch_pool.acquire() as adapter:
            return await adapter.analyze_position(
                fen, nodes=nodes, depth=depth, multipv=multipv, time_ms=time_ms
            )
