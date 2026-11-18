from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import chess.engine
import pytest

from scan64.content.puzzle_practice import THEME_SKILLS
from scan64.learning.diagnosis.trust import HISTORICALLY_TRUSTED_CODES, TRUSTED_CODES
from scan64.learning.evaluation import m94_protocol, real_games
from scan64.learning.evaluation.m94_reference import reference_labels


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
    passing = {code for code in m94_protocol.CODES if scores[code].trusted}
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
        source = real_games.M94_CORPUS.with_name(f"m94_v2_fresh80_{artifact}")
        shutil.copyfile(source, tmp_path / source.name)
    changed = tmp_path / f"m94_v2_fresh80_{suffix}"
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


def test_reference_recognizes_ep_clearing_two_blockers_for_discovered_mate() -> None:
    before = chess.Board("1N6/8/2K5/k4pPR/pp6/8/8/8 w - f6 0 2")
    after = before.copy(stack=False)
    after.push_uci("b8d7")
    assert reference_labels(before, after, [{"score_mate": 1, "pv": ["g5f6"]}]) == {
        "tactics.discovered_attack"
    }


def test_ep_capture_without_root_origin_on_stationary_ray_is_not_discovery() -> None:
    before = chess.Board("k7/7B/8/5pP1/8/3r4/8/7K w - f6 0 2")
    after = before.copy(stack=False)
    after.push_uci("h1g1")
    # EP clears f5 from h7-d3, but the mover's origin g5 is not on that ray.
    assert "tactics.discovered_attack" not in reference_labels(
        before, after, [{"score_cp": 500, "pv": ["g5f6", "a8b8", "h7d3"]}]
    )


def test_reference_rejects_invalid_source_board() -> None:
    before = chess.Board("4q2r/8/8/8/8/8/8/R2K4 w - - 0 1")
    after = before.copy(stack=False)
    after.push_uci("d1c1")
    assert (
        reference_labels(before, after, [{"score_cp": 500, "pv": ["a1a8", "e8e7", "a8h8"]}])
        == set()
    )
