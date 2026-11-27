"""An XBoard engine runs Scan64's single-line analysis through a real process.

The engine is ``tests/fixtures/engines/fake_xboard.py`` behind a platform
launcher (``tests.support.engines``), so the protocol, process start and option
mapping are exercised end to end without a third-party engine on the machine.
"""

from __future__ import annotations

from pathlib import Path

import chess
import pytest

from scan64.chess.opponents.protocols import OpponentContext
from scan64.providers.engine.config import (
    EngineConfig,
    EngineConfigurationError,
    load_engine_profile,
)
from scan64.providers.engine.uci import EngineCapabilityError
from scan64.providers.stockfish.adapter import StockfishAdapter
from scan64.providers.stockfish.pool import PooledStockfishAdapter
from tests.support.engines import fake_xboard_launcher


@pytest.fixture
def xboard_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    return fake_xboard_launcher(tmp_path, monkeypatch)


def xboard_config(binary: Path) -> EngineConfig:
    return EngineConfig(binary_path=str(binary), protocol="xboard", threads=2, hash_size=32)


def test_profile_selects_the_protocol(tmp_path: Path) -> None:
    profile = tmp_path / "xb.toml"
    profile.write_text('[engine]\nname = "xb"\nbinary_path = "fairymax"\nprotocol = "xboard"\n')
    assert load_engine_profile(profile).protocol == "xboard"
    profile.write_text('[engine]\nname = "u"\nbinary_path = "stockfish"\n')
    assert load_engine_profile(profile).protocol == "uci"
    profile.write_text('[engine]\nname = "w"\nbinary_path = "x"\nprotocol = "winboard"\n')
    with pytest.raises(EngineConfigurationError, match="protocol must be one of uci, xboard"):
        load_engine_profile(profile)


@pytest.mark.asyncio
async def test_xboard_engine_gives_single_line_analysis(xboard_binary: tuple[Path, Path]) -> None:
    binary, log = xboard_binary
    board = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
    analysis = await StockfishAdapter(xboard_config(binary)).analyze_position(
        board.fen(), nodes=5000
    )
    [line] = analysis.raw_result
    assert chess.Move.from_uci(line["pv"][0]) in board.legal_moves
    assert line["score_cp"] == 37
    assert analysis.config["engine_name"] == "FakeXBoard 1"
    assert analysis.config["protocol"] == "xboard"
    assert analysis.config["nodes"] == 5000
    sent = log.read_text(encoding="utf-8").splitlines()
    assert "cores 2" in sent
    assert "memory 32" in sent
    assert f"setboard {board.fen()}" in sent
    assert "analyze" in sent
    assert not any("MultiPV" in command for command in sent)


@pytest.mark.asyncio
async def test_xboard_engine_refuses_multiple_lines(xboard_binary: tuple[Path, Path]) -> None:
    binary, _ = xboard_binary
    with pytest.raises(EngineCapabilityError, match="4 analysis lines need MultiPV"):
        await StockfishAdapter(xboard_config(binary)).analyze_position(
            chess.STARTING_FEN, nodes=5000, multipv=4
        )


@pytest.mark.asyncio
async def test_pooled_xboard_engine_analyses_and_names_missing_bot_controls(
    xboard_binary: tuple[Path, Path],
) -> None:
    binary, _ = xboard_binary
    adapter = PooledStockfishAdapter(xboard_config(binary))
    try:
        analysis = await adapter.analyze_position(chess.STARTING_FEN, nodes=5000)
        assert len(analysis.raw_result) == 1
        with pytest.raises(EngineCapabilityError, match="MultiPV"):
            await adapter.play_bot_move(chess.STARTING_FEN, OpponentContext(0, bot_level=3))
    finally:
        await adapter.quit()
