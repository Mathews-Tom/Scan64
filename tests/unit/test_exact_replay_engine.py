"""An exact-replay lesson records the engine that was configured, never a literal."""

from __future__ import annotations

from pathlib import Path

import pytest
from chess_lesson_spec import Diagnosis

from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.providers.engine import ENGINE_CONFIG_ENV

FEN = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 1 5"
DIAGNOSIS = Diagnosis(primary="tactics.knight_fork", confidence=0.9, evidence_refs=["ev_1"])


def _profile(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "engine.toml"
    path.write_text(f"[engine]\nname = '{name}'\nbinary_path = 'caissa'\n", encoding="utf-8")
    return path


async def _lesson(engine: str | None = None) -> str | None:
    lesson = await generate_exact_replay_exercise(
        diagnosis=DIAGNOSIS,
        fen=FEN,
        lesson_id="les_engine",
        accepted_moves_san=["Nc3"],
        engine=engine,
    )
    assert lesson.verification.status == "unverified"
    return lesson.verification.engine


@pytest.mark.asyncio
async def test_without_a_profile_the_engine_is_the_default_stockfish() -> None:
    assert await _lesson() == "stockfish"


@pytest.mark.asyncio
async def test_the_active_profile_names_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(_profile(tmp_path, "caissa")))
    assert await _lesson() == "caissa"


@pytest.mark.asyncio
async def test_an_explicit_engine_wins_over_the_active_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A running pool keeps its engine when the profile file changes.
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(_profile(tmp_path, "caissa")))
    assert await _lesson(engine="stockfish") == "stockfish"
