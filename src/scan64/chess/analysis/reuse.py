"""Read-only same-owner batch reuse; completed calls are written by the job."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import chess
from sqlmodel import Session, col, select

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.providers.stockfish.adapter import (
    EngineAdapter,
    IdentifiedEngineAdapter,
    search_board,
    variant_config,
)


def _key(fen: str, config: dict[str, Any]) -> tuple[str, str] | None:
    version = config.get("engine_version")
    if (
        not isinstance(version, str)
        or not version.startswith("sha256:")
        or len(version) != 71
        or any(char not in "0123456789abcdef" for char in version[7:])
        or not isinstance(config.get("options"), dict)
        or not config["options"]
        or not {"engine_name", "network", "nodes", "depth", "time_ms", "multipv"} <= config.keys()
    ):
        # Unknown/partial legacy identities cannot prove an exact-key match.
        return None
    return fen, json.dumps(config, sort_keys=True, separators=(",", ":"))


class BatchAnalysisAdapter:
    """An EngineAdapter facade confined to one game's final-write transaction.

    Fetch projections in bounded batches before searching, never ORM source
    objects. Every hit is a new detached occurrence row with independent JSON.
    The call ledger also retains verification searches whose candidates are
    later rejected, without adding/flushing anything while an engine runs.
    """

    def __init__(self, adapter: EngineAdapter, game: Game, session: Session) -> None:
        with session.no_autoflush:
            owner = game.owner_player_id
            initial_fen = game.headers.get("FEN")
            moves = list(game.moves)
        if owner is None:
            raise ValueError("Cannot reuse analyses without a game owner")
        self.adapter = adapter
        self.completed: list[tuple[str, EngineAnalysis]] = []
        self._saved: dict[tuple[str, str], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        self._owner = owner
        self._loaded: set[str] = set()
        board = board_from(initial_fen)
        fens = [board.fen()]
        for move in moves:
            board.push_uci(move)
            fens.append(board.fen())
        self.prefetch(session, fens)

    def prefetch(self, session: Session, fens: list[str] | tuple[str, ...]) -> None:
        """Load the owner's saved searches of ``fens`` (for example line positions, #367)."""
        unique_fens = [fen for fen in dict.fromkeys(fens) if fen not in self._loaded]
        self._loaded.update(unique_fens)
        with session.no_autoflush:
            for offset in range(0, len(unique_fens), 400):
                rows = session.exec(
                    select(Position.fen, EngineAnalysis.config, EngineAnalysis.raw_result)
                    .join(EngineAnalysis, col(EngineAnalysis.position_id) == col(Position.id))
                    .join(Game, col(Position.game_id) == col(Game.id))
                    .where(
                        Game.owner_player_id == self._owner,
                        col(Position.fen).in_(unique_fens[offset : offset + 400]),
                    )
                    .order_by(col(EngineAnalysis.created_at), col(EngineAnalysis.id))
                ).all()
                for fen, saved_config, raw_result in rows:
                    config = dict(saved_config)
                    key = _key(fen, config)
                    if key is not None:
                        # Prefer the first durable search at this exact key.
                        self._saved.setdefault(key, (config, list(raw_result)))

    async def analyze_position(
        self,
        fen: str | chess.Board,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        target = fen
        board = search_board(fen)
        fen = fen if isinstance(fen, str) else board.fen()
        saved = None
        if self._saved and isinstance(self.adapter, IdentifiedEngineAdapter):
            config = dict(
                await self.adapter.analysis_identity(),
                nodes=nodes,
                depth=depth,
                time_ms=time_ms,
                multipv=multipv,
                **variant_config(board),
            )
            key = _key(fen, config)
            if key is not None:
                saved = self._saved.get(key)
        if saved is not None:
            config, raw_result = saved
            analysis = EngineAnalysis(
                position_id=None, config=deepcopy(config), raw_result=deepcopy(raw_result)
            )
        else:
            analysis = await self.adapter.analyze_position(
                target, nodes=nodes, depth=depth, time_ms=time_ms, multipv=multipv
            )
            key = _key(fen, analysis.config)
            if key is not None:
                self._saved.setdefault(
                    key, (deepcopy(analysis.config), deepcopy(analysis.raw_result))
                )
        self.completed.append((fen, analysis))
        return analysis
