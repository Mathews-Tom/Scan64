from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import chess
import chess.pgn
import pytest

from scripts import record_diagnosis_reference as recorder


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [None, "original", "m78"])
async def test_m79_selects_only_qualifying_ordinals_121_through_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str | None
) -> None:
    games = []
    for ordinal in range(1, 201):
        board = chess.Board()
        for _ in range(5):
            for san in ("Nf3", "Nf6", "Ng1", "Ng8"):
                board.push_san(san)
        game = chess.pgn.Game.from_board(board)
        game.headers.update(Site=f"game-{ordinal}", WhiteElo="600", BlackElo="1600")
        games.append(game)
    invalid = chess.pgn.Game.from_board(board)
    invalid.headers.update(Site="excluded", WhiteElo="599", BlackElo="1600")
    source = io.StringIO("\n\n".join(str(game) for game in [invalid, *games]))

    @contextmanager
    def verified(_source: Path) -> Any:
        yield source

    def no_engine(_config: object) -> Any:
        raise RuntimeError("stop before recording any engine output")

    original = [{"id": f"game-{ordinal}"} for ordinal in range(1, 41)]
    m78 = [{"id": f"game-{ordinal}"} for ordinal in range(41, 121)]
    if mismatch == "original":
        original[0]["id"] = "wrong"
    if mismatch == "m78":
        m78[0]["id"] = "wrong"
    for name, prefix in (("recorded", original), ("fresh80_recorded", m78)):
        (tmp_path / f"{name}.json").write_text(json.dumps({"games": prefix}))
    monkeypatch.setattr(recorder, "DEST", tmp_path)
    monkeypatch.setattr(recorder, "verified_pgn", verified)
    monkeypatch.setattr(recorder, "PooledStockfishAdapter", no_engine)
    if mismatch:
        with pytest.raises(ValueError, match="prefix differs"):
            await recorder.record(tmp_path / "source.zst", m79_fresh80=True)
        assert not (tmp_path / "m79_fresh80_real_games.pgn").exists()
    else:
        with pytest.raises(RuntimeError, match="stop before recording"):
            await recorder.record(tmp_path / "source.zst", m79_fresh80=True)
        with (tmp_path / "m79_fresh80_real_games.pgn").open() as stream:
            selected = []
            while game := chess.pgn.read_game(stream):
                selected.append(game.headers["Site"])
        assert selected == [f"game-{ordinal}" for ordinal in range(121, 201)]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["real_games.pgn", "recorded.json"])
async def test_m79_refuses_to_overwrite_either_holdout_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    path = tmp_path / f"m79_fresh80_{suffix}"
    path.write_text("preserved")
    monkeypatch.setattr(recorder, "DEST", tmp_path)
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        await recorder.record(tmp_path / "missing-source.zst", m79_fresh80=True)
    assert path.read_text() == "preserved"
