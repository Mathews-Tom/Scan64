from __future__ import annotations

import pytest
from sqlmodel import Session

from scan64.chess.analysis.jobs import run_analysis_for_game
from scan64.chess.games.models import Game


@pytest.mark.asyncio
async def test_analysis_job_rejects_ownerless_game() -> None:
    with pytest.raises(ValueError, match="without an owner"):
        await run_analysis_for_game(Game(pgn=""), Session())
