from dataclasses import dataclass
from typing import Any

import chess
import chess.engine

from src.scan64.chess.analysis.models import EngineAnalysis, EngineAnalysisConfig


@dataclass
class StockfishConfig:
    binary_path: str = "stockfish"
    threads: int = 1
    hash_size: int = 16


class StockfishAdapter:
    def __init__(self, config: StockfishConfig):
        self.config = config

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        _, engine = await chess.engine.popen_uci(self.config.binary_path)
        try:
            await engine.configure({"Threads": self.config.threads, "Hash": self.config.hash_size})

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

            engine_name = engine.id.get("name", "Stockfish")

            config_dict = EngineAnalysisConfig(
                engine_name=engine_name,
                engine_version="unknown",  # We can try to parse from name
                nodes=nodes,
                depth=depth,
                time_ms=time_ms,
                multipv=multipv,
            ).model_dump()

            return EngineAnalysis(
                position_id=None,  # To be filled by the caller
                config=config_dict,
                raw_result=raw_result,
            )
        finally:
            await engine.quit()
