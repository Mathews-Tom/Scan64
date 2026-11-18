from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import chess.engine
import pytest

from scan64.content.puzzle_practice import THEME_SKILLS
from scan64.learning.diagnosis.trust import HISTORICALLY_TRUSTED_CODES, TRUSTED_CODES
from scan64.learning.evaluation import m94_protocol, real_games


@pytest.mark.asyncio
async def test_single_measured_holdout_replays_without_engine_or_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_engine(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Replay launched an engine")

    def forbidden_git(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Replay depended on Git history")

    monkeypatch.setattr(chess.engine, "popen_uci", forbidden_engine)
    monkeypatch.setattr(m94_protocol.subprocess, "run", forbidden_git)
    monkeypatch.setattr(m94_protocol.subprocess, "check_output", forbidden_git)
    assert real_games.M94_MEASUREMENT is not None
    scores = await real_games.measure(real_games.M94_CORPUS)
    expected = {
        "tactics.skewer": (2, 0, 0),
        "tactics.discovered_attack": (0, 1, 3),
        "tactics.back_rank": (1, 0, 0),
    }
    assert {
        code: (scores[code].tp, scores[code].fp, scores[code].fn) for code in m94_protocol.CODES
    } == expected
    passing = {code for code in m94_protocol.CODES if scores[code].trusted}
    assert passing == set()
    assert TRUSTED_CODES & m94_protocol.CODES == passing
    assert HISTORICALLY_TRUSTED_CODES & m94_protocol.CODES == passing
    mapped = {code for codes in THEME_SKILLS.values() for code in codes}
    assert mapped & m94_protocol.CODES == passing
    assert "tactics.fork.knight" in TRUSTED_CODES & HISTORICALLY_TRUSTED_CODES
    assert THEME_SKILLS["fork"] == {"tactics.fork.knight"}


@pytest.mark.parametrize("suffix", ["real_games.pgn", "recorded.json", "manifest.json"])
@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_measured_replay_rejects_missing_or_changed_real_artifacts(
    tmp_path: Path, suffix: str, mutation: str
) -> None:
    for artifact in ("real_games.pgn", "recorded.json", "manifest.json"):
        source = real_games.M94_CORPUS.with_name(f"m94_fresh80_{artifact}")
        shutil.copyfile(source, tmp_path / source.name)
    changed = tmp_path / f"m94_fresh80_{suffix}"
    if mutation == "missing":
        changed.unlink()
        error: type[Exception] = FileNotFoundError
    else:
        changed.write_bytes(changed.read_bytes() + b" ")
        error = ValueError
    assert real_games.M94_MEASUREMENT is not None
    with pytest.raises(error):
        m94_protocol.validate_recording(
            tmp_path / real_games.M94_CORPUS.name, (), real_games.M94_MEASUREMENT
        )
