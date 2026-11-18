from __future__ import annotations

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

import pytest

from scan64.providers.stockfish.adapter import StockfishConfig
from scripts import record_diagnosis_reference as recorder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine,threads,hash_mb",
    [("Stockfish 18", 1, 16), ("Stockfish 19", 2, 16), ("Stockfish 19", 1, 32)],
)
async def test_wrong_engine_or_config_cannot_consume_the_one_shot_holdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, threads: int, hash_mb: int
) -> None:
    # Selection is legal synthetic PGN, never ordinals from the real source.
    moves = (
        "1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 5. Nf3 Nf6 "
        "6. Ng1 Ng8 7. Nf3 Nf6 8. Ng1 Ng8 9. Nf3 Nf6 10. Ng1 Ng8 *"
    )
    games = [
        f'[Site "synthetic-{index}"]\n[WhiteElo "1000"]\n[BlackElo "1000"]\n\n{moves}\n'
        for index in range(360)
    ]
    for name, start, end in [
        ("recorded.json", 0, 40),
        ("fresh80_recorded.json", 40, 120),
        ("m79_fresh80_recorded.json", 120, 200),
        ("m93_fresh80_recorded.json", 200, 280),
    ]:
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "provenance": {"engine": "Stockfish 19"},
                    "games": [{"id": f"synthetic-{index}"} for index in range(start, end)],
                }
            )
        )

    @contextmanager
    def source(_: Path) -> Iterator[TextIO]:
        with io.StringIO("\n".join(games)) as stream:
            yield stream

    calls: list[str] = []

    class IdentityProbe:
        async def analysis_identity(self) -> dict[str, Any]:
            calls.append("identity")
            return {"engine_name": engine}

        async def analyze_position(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("Identity preflight performed a position search")

        async def quit(self) -> None:
            calls.append("quit")

    monkeypatch.setattr(recorder, "DEST", tmp_path)
    monkeypatch.setattr(recorder, "verified_pgn", source)
    monkeypatch.setattr(recorder.m94_protocol, "validate_frozen_sources", lambda root: None)
    monkeypatch.setattr(
        recorder, "StockfishConfig", lambda: StockfishConfig(threads=threads, hash_size=hash_mb)
    )
    monkeypatch.setattr(recorder, "PooledStockfishAdapter", lambda config: IdentityProbe())
    message = "Stockfish 19" if engine != "Stockfish 19" else "one thread and 16 MB"
    with pytest.raises(ValueError, match=message):
        await recorder.record(tmp_path / "synthetic-source", m94_fresh80=True)
    assert calls == (["identity", "quit"] if engine != "Stockfish 19" else [])
    assert not any(tmp_path.glob("m94_fresh80_*"))
