import asyncio
import dataclasses
import os
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import chess
import chess.engine

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.opponents.bot_policy import choose_elo_move, choose_ladder_move
from scan64.chess.opponents.protocols import OpponentContext
from scan64.providers.engine.config import EngineConfigurationError, UciOptionValue, scaled_nodes
from scan64.providers.engine.uci import (
    ELO_LADDER_OPTIONS,
    FULL_STRENGTH,
    LADDER_OPTIONS,
    LEGACY_STRENGTH_OPTIONS,
    Engine,
    EngineCapabilityError,
    full_strength_options,
    missing_options,
    open_engine,
    require_options,
    startup_options,
    within_declared_range,
)
from scan64.providers.stockfish.adapter import StockfishConfig, search, search_limit
from scan64.providers.stockfish.identity import analysis_identity, engine_identity

DEFAULT_INTERACTIVE_CONCURRENCY = 2
HINT_CACHE_SIZE = 128
HINT_NODES = 200_000
HINT_TIME_MS = 1500
HINT_MULTIPV = 4
HintCacheKey = tuple[
    str, tuple[str, ...], bool, tuple[object, ...], int | None, int | None, int, int | None
]


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
    """Batch size reserves the interactive engines and one CPU for the application."""
    affinity = getattr(os, "sched_getaffinity", None)
    available_cpus = len(affinity(0)) if affinity is not None else (os.cpu_count() or 1)
    default = max(2, available_cpus - interactive_concurrency() - 1)
    return _positive_int_from_env("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", default)


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
        self._engine: Engine | None = None
        self._identity: dict[str, Any] | None = None
        self.game_token: object | None = None

    async def ensure_started(self) -> None:
        if self._engine is None:
            engine = await open_engine(self.config)
            try:
                await engine.configure(startup_options(engine, self.config))
                self._identity = engine_identity(engine, self.config.binary_path)
            except BaseException:
                await engine.quit()
                raise
            self._engine = engine

    async def analysis_identity(self) -> dict[str, Any]:
        await self.ensure_started()
        assert self._engine is not None and self._identity is not None
        return analysis_identity(
            self._engine, self._identity, self.config.threads, self.config.node_scale
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

    def discard(self) -> None:
        """Forget a process that has exited; the next checkout starts a new one."""
        self._engine = None
        self._identity = None

    async def quit(self) -> None:
        if self._engine is not None:
            await self._engine.quit()
            self._engine = None
            self._identity = None

    async def play_move(
        self, fen: str, skill_level: int, limit: chess.engine.Limit
    ) -> chess.engine.PlayResult:
        await self.ensure_started()
        assert self._engine is not None
        engine = self._engine
        require_options(engine, LEGACY_STRENGTH_OPTIONS, "Skill Level opponent play")
        options: dict[str, UciOptionValue] = full_strength_options(engine, self.config.threads)
        options["Skill Level"] = skill_level
        await engine.configure(options)
        board = chess.Board(fen)
        scaled = dataclasses.replace(limit, nodes=scaled_nodes(limit.nodes, self.config.node_scale))
        return await self._engine.play(
            board, scaled, info=chess.engine.INFO_SCORE, game=self.game_token
        )

    async def play_bot_move(self, fen: str, context: OpponentContext) -> chess.engine.PlayResult:
        await self.ensure_started()
        assert self._engine is not None
        engine = self._engine
        if self.config.ladder.strength == "elo":
            return await self._play_elo_bot_move(engine, fen, context)
        try:
            return await choose_ladder_move(
                engine,
                chess.Board(fen),
                context,
                self.game_token,
                node_scale=self.config.node_scale,
            )
        except chess.engine.EngineError as error:
            # The ladder resets Stockfish's strength controls and searches every
            # legal move; name the missing option instead of a raw UCI error.
            missing = missing_options(engine, (*FULL_STRENGTH, *LADDER_OPTIONS))
            if missing:
                elo_hint = (
                    ""
                    if missing_options(engine, ELO_LADDER_OPTIONS)
                    else '; it declares UCI_Elo, so set [ladder] strength = "elo" in its profile'
                )
                raise EngineCapabilityError(
                    f"The bot ladder requires UCI option(s) {', '.join(missing)}, which "
                    f"{engine.id.get('name', 'the configured engine')} does not declare"
                    f"{elo_hint}"
                ) from error
            raise

    async def _play_elo_bot_move(
        self, engine: Engine, fen: str, context: OpponentContext
    ) -> chess.engine.PlayResult:
        require_options(engine, ELO_LADDER_OPTIONS, "The Elo bot ladder")
        assert context.bot_level is not None
        ladder = self.config.ladder
        assert ladder.movetime_ms is not None
        elo = ladder.elo_for(context.bot_level)
        if within_declared_range(engine, "UCI_Elo", elo) != elo:
            declared = engine.options["UCI_Elo"]
            raise EngineConfigurationError(
                f"Engine profile {self.config.name!r} gives bot level {context.bot_level} "
                f"UCI_Elo {elo}, outside the {declared.min}-{declared.max} that "
                f"{engine.id.get('name', 'the configured engine')} declares"
            )
        return await choose_elo_move(
            engine,
            chess.Board(fen),
            context,
            self.game_token,
            elo=elo,
            movetime_ms=ladder.movetime_ms,
        )

    async def analyze_position(
        self,
        fen: str | chess.Board,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        """Analyze an isolated FEN or a replay board without discarding its stack."""
        await self.ensure_started()
        assert self._engine is not None
        engine = self._engine
        await engine.configure(full_strength_options(engine, self.config.threads))
        board = fen if isinstance(fen, chess.Board) else chess.Board(fen)
        limit = search_limit(nodes, depth, time_ms, node_scale=self.config.node_scale)
        raw_result = await search(
            engine,
            board,
            limit,
            multipv,
            game=self.game_token,
            deadline_s=self.config.search_deadline_s,
        )

        config_dict = dict(
            await self.analysis_identity(),
            nodes=nodes,
            depth=depth,
            time_ms=time_ms,
            multipv=multipv,
        )

        return EngineAnalysis(position_id=None, config=config_dict, raw_result=raw_result)


class EnginePool:
    def __init__(self, config: StockfishConfig, concurrency: int):
        self.config = config
        self.concurrency = concurrency
        self._queue: asyncio.Queue[PooledStockfishAdapter] = asyncio.Queue()
        self._adapters: list[PooledStockfishAdapter] = []
        self._initialized = False
        self.closed = False

    async def initialize(self) -> None:
        # Set before the first await: cold opponent and hint checkouts must
        # share one startup, not each create another full pool of engines.
        if not hasattr(self, "_initialize_lock"):
            self._initialize_lock = asyncio.Lock()
        async with self._initialize_lock:
            if self._initialized:
                return
            # A previous startup rollback can itself have been cancelled.
            await self._close_adapters()
            try:
                for _ in range(self.concurrency):
                    adapter = PooledStockfishAdapter(self.config)
                    self._adapters.append(adapter)
                    await adapter.ensure_started()
            except BaseException:
                # Retain ownership until quit completes, even if cleanup is cancelled again.
                await self._close_adapters()
                raise
            for adapter in self._adapters:
                self._queue.put_nowait(adapter)
            self._initialized = True

    async def _close_adapters(self) -> None:
        while self._adapters:
            await self._adapters[-1].quit()
            self._adapters.pop()
        while not self._queue.empty():
            self._queue.get_nowait()

    async def close(self) -> None:
        await self._close_adapters()
        self.closed = True

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[PooledStockfishAdapter, None]:
        await self.initialize()
        adapter = await self._queue.get()
        try:
            await adapter.reset()
            yield adapter
        except chess.engine.EngineTerminatedError:
            # The caller still gets the error; later checkouts get a live engine.
            adapter.discard()
            raise
        finally:
            self._queue.put_nowait(adapter)


class PoolBoundAdapter:
    """``EngineAdapter``-shaped facade that checks an engine out of an
    ``EnginePool`` for the duration of a single ``analyze_position`` call.
    Lets `FastPassOrchestrator`/`FocusedPassOrchestrator` route through a
    pool without knowing pools exist."""

    def __init__(self, pool: EnginePool):
        self._pool = pool

    async def analysis_identity(self) -> dict[str, Any]:
        async with self._pool.acquire() as adapter:
            return await adapter.analysis_identity()

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
        # Only public engine inputs/results; never player IDs, hint steps,
        # session snapshots or history-dependent nudge projections.
        self._hint_cache: OrderedDict[HintCacheKey, EngineAnalysis] = OrderedDict()

    @classmethod
    def from_env(cls, config: StockfishConfig) -> "EnginePoolManager":
        """Size the interactive pool from its override (default 2), and the batch
        pool from its override or available CPUs minus interactive capacity and
        one application CPU (minimum 2). Direct constructors keep explicit sizes."""
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
        self._hint_cache.clear()

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

    async def play_bot_interactive(
        self, fen: str, context: OpponentContext
    ) -> chess.engine.PlayResult:
        async with self.interactive_pool.acquire() as adapter:
            return await adapter.play_bot_move(fen, context)

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

    _hint_in_progress = False

    def _hint_limits(self) -> tuple[int | None, int | None, int]:
        """Profile ``[limits.hint]`` when set, else the built-in hint budget."""
        configured = self.config.limits.get("hint")
        if configured is None:
            return HINT_NODES, HINT_TIME_MS, HINT_MULTIPV
        return configured.nodes, configured.time_ms, configured.multipv

    def hint_limits(self) -> tuple[int | None, int | None, int]:
        """The hint budget as ``(nodes, time_ms, multipv)``; requests may only narrow it."""
        return self._hint_limits()

    def _narrowed_hint_limits(
        self, nodes: int | None, depth: int | None
    ) -> tuple[int | None, int | None, int, int | None]:
        """Effective ``(nodes, time_ms, multipv, depth)``: overrides narrow, never widen."""
        budget_nodes, time_ms, multipv = self._hint_limits()
        if nodes is not None:
            if nodes < 1:
                raise ValueError("Hint node budget must be positive")
            if budget_nodes is not None and nodes > budget_nodes:
                raise ValueError(f"Hint node budget exceeds the {budget_nodes}-node limit")
        if depth is not None and depth < 1:
            raise ValueError("Hint depth must be positive")
        return (nodes if nodes is not None else budget_nodes), time_ms, multipv, depth

    def _hint_cache_key(
        self, board: chess.Board, nodes: int | None = None, depth: int | None = None
    ) -> HintCacheKey:
        return (
            board.root().fen(en_passant="fen"),
            tuple(move.uci() for move in board.move_stack),
            board.chess960,
            self.config.fingerprint(),
            *self._narrowed_hint_limits(nodes, depth),
        )

    def invalidate_hint(
        self, board: chess.Board, *, nodes: int | None = None, depth: int | None = None
    ) -> None:
        """Discard rejected output for this exact replay, not just its final FEN."""
        self._hint_cache.pop(self._hint_cache_key(board, nodes, depth), None)

    async def analyze_hint(
        self, board: chess.Board, *, nodes: int | None = None, depth: int | None = None
    ) -> EngineAnalysis:
        """Admit one hint, leaving an interactive slot for opponent moves.

        No hint queue: reject concurrent hints or a single-engine deployment.
        Acquisition waits at most 250 ms; search is 200k nodes / 1.5 seconds,
        with a three-second wall deadline (including lazy engine startup).
        Replay history reaches the UCI position command and is part of cache identity.
        ``nodes`` and ``depth`` narrow the hint budget (``ValueError`` if ``nodes``
        exceeds it); the time limit and the deadline still apply.
        """
        board = board.copy()
        key = self._hint_cache_key(board, nodes, depth)
        cached = self._hint_cache.get(key)
        if cached is not None:
            self._hint_cache.move_to_end(key)
            return cached.model_copy(deep=True)
        if self.interactive_pool.concurrency < 2 or self._hint_in_progress:
            raise TimeoutError("Hint capacity is busy")
        self._hint_in_progress = True
        try:
            async with asyncio.timeout(3):
                await self.interactive_pool.initialize()
                lease = self.interactive_pool.acquire()
                async with asyncio.timeout(0.25):
                    adapter = await lease.__aenter__()
                try:
                    search_nodes, time_ms, multipv, search_depth = self._narrowed_hint_limits(
                        nodes, depth
                    )
                    # Depth is sent only when requested: the default hint search is unchanged.
                    if search_depth is None:
                        analysis = await adapter.analyze_position(
                            board, nodes=search_nodes, time_ms=time_ms, multipv=multipv
                        )
                    else:
                        analysis = await adapter.analyze_position(
                            board,
                            nodes=search_nodes,
                            depth=search_depth,
                            time_ms=time_ms,
                            multipv=multipv,
                        )
                    self._hint_cache[key] = analysis.model_copy(deep=True)
                    if len(self._hint_cache) > HINT_CACHE_SIZE:
                        self._hint_cache.popitem(last=False)
                    return analysis
                finally:
                    await lease.__aexit__(None, None, None)
        finally:
            self._hint_in_progress = False


PooledUciEngineAdapter = PooledStockfishAdapter
