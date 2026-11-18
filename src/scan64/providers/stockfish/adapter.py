import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import chess
import chess.engine

from scan64.chess.analysis.models import EngineAnalysis
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


@dataclass
class StockfishConfig:
    binary_path: str = field(
        default_factory=lambda: os.environ.get("SCAN64_STOCKFISH_PATH", "stockfish")
    )
    threads: int = 1
    hash_size: int = 16


class StockfishAdapter:
    def __init__(self, config: StockfishConfig):
        self.config = config
        self._identity: dict[str, Any] | None = None
        self._identity_signature: ExecutableSignature | None = None

    async def analysis_identity(self) -> dict[str, Any]:
        # Unlike a live pooled process, a per-call adapter has no loaded build
        # between calls. A replaced executable must invalidate its probe.
        signature = executable_signature(self.config.binary_path)
        if self._identity is None or self._identity_signature != signature:
            _, engine = await chess.engine.popen_uci(self.config.binary_path)
            try:
                await engine.configure(
                    {"Threads": self.config.threads, "Hash": self.config.hash_size}
                )
                self._identity = analysis_identity(
                    engine, engine_identity(engine, self.config.binary_path), self.config.threads
                )
                self._identity_signature = signature
            finally:
                await engine.quit()
        identity = dict(self._identity)
        identity["options"] = dict(
            identity["options"], Threads=self.config.threads, Hash=self.config.hash_size
        )
        return identity

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        signature = executable_signature(self.config.binary_path)
        _, engine = await chess.engine.popen_uci(self.config.binary_path)
        try:
            await engine.configure({"Threads": self.config.threads, "Hash": self.config.hash_size})
            self._identity = analysis_identity(
                engine, engine_identity(engine, self.config.binary_path), self.config.threads
            )
            self._identity_signature = signature

            board = chess.Board(fen)
            limit = chess.engine.Limit(
                nodes=nodes, depth=depth, time=time_ms / 1000.0 if time_ms else None
            )

            info_result = await engine.analyse(board, limit, multipv=multipv)

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
