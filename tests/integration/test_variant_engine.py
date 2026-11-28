"""A variant search reaches only an engine that declares that variant (#398)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import chess
import chess.engine
import chess.variant
import pytest

from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.explanations.themes import GameTrace, game_trace
from scan64.providers.engine import EngineCapabilityError
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig, search
from scan64.providers.stockfish.pool import EnginePool, PoolBoundAdapter
from tests.support.engines import fake_uci_launcher

LIMIT = chess.engine.Limit(nodes=1)


async def _run(launcher: Path, board: chess.Board) -> list[dict[str, object]]:
    _, engine = await chess.engine.popen_uci(str(launcher))
    try:
        return await search(engine, board, LIMIT, 1, deadline_s=5, score_scale=1)
    finally:
        await engine.quit()


def _search(launcher: Path, board: chess.Board) -> list[dict[str, object]]:
    return asyncio.run(_run(launcher, board))


def test_a_declared_variant_is_searched_under_its_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_UCI_VARIANTS", "chess,crazyhouse,3check")
    launcher, log = fake_uci_launcher(tmp_path, monkeypatch)
    board = chess.variant.CrazyhouseBoard(
        "rnbqkbnr/ppp1pppp/8/8/8/8/PPPP1PPP/RNBQKBNR[Pp] w KQkq - 0 3"
    )
    lines = _search(launcher, board)
    assert lines and board.is_legal(chess.Move.from_uci(str(lines[0]["pv"][0])))  # type: ignore[index]
    assert "setoption name UCI_Variant value crazyhouse" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("variants", ["", "chess,crazyhouse"])
def test_an_undeclared_variant_is_refused_before_any_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variants: str
) -> None:
    monkeypatch.setenv("FAKE_UCI_VARIANTS", variants)
    launcher, log = fake_uci_launcher(tmp_path, monkeypatch)
    with pytest.raises(EngineCapabilityError, match="Analysing atomic needs an engine"):
        _search(launcher, chess.variant.AtomicBoard())
    assert "go" not in log.read_text(encoding="utf-8").split()


def test_standard_chess_needs_no_variant_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FAKE_UCI_VARIANTS", raising=False)
    launcher, log = fake_uci_launcher(tmp_path, monkeypatch)
    assert _search(launcher, chess.Board())
    assert "UCI_Variant" not in log.read_text(encoding="utf-8")


# 1.e4 d5 2.exd5 Qxd5 3.Nc3 Qa5 4.P@b4: a drop, illegal in standard chess.
_DROP_GAME = ["e2e4", "d7d5", "e4d5", "d8d5", "b1c3", "d5a5", "P@b4"]


@pytest.mark.parametrize("pooled", [True, False])
def test_a_variant_fast_pass_scores_the_trace_of_that_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pooled: bool
) -> None:
    """#506: the fast pass searches a crazyhouse game's boards under its rules."""
    monkeypatch.setenv("FAKE_UCI_VARIANTS", "chess,crazyhouse")
    launcher, log = fake_uci_launcher(tmp_path, monkeypatch)
    san = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5", "P@b4"]
    config = StockfishConfig(binary_path=str(launcher), threads=1, hash_size=16)

    async def run() -> GameTrace:
        pool = EnginePool(config, 1)
        adapter = PoolBoundAdapter(pool) if pooled else StockfishAdapter(config)
        try:
            result = await FastPassOrchestrator(adapter, FastPassConfig(nodes=1)).run_fast_pass(
                san, variant="crazyhouse"
            )
        finally:
            await pool.close()
        with pytest.raises(ValueError, match="cannot use the scores of a crazyhouse search"):
            game_trace(None, _DROP_GAME[:6], result.evaluations)
        return game_trace(None, _DROP_GAME, result.evaluations, "crazyhouse")

    trace = asyncio.run(run())
    assert trace.variant == "crazyhouse"
    assert trace.scores == (0,) * 8
    assert "setoption name UCI_Variant value crazyhouse" in log.read_text(encoding="utf-8")
