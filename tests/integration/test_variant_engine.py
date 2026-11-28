"""A variant search reaches only an engine that declares that variant (#398)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import chess
import chess.engine
import chess.variant
import pytest

from scan64.providers.engine import EngineCapabilityError
from scan64.providers.stockfish.adapter import search
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
