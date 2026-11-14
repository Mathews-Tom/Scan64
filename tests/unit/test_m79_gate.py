from __future__ import annotations

import json
from pathlib import Path

import pytest

from scan64.learning.diagnosis import trust
from scan64.learning.evaluation import real_games


@pytest.mark.parametrize("tp,fp,trusted", [(8, 2, True), (7, 3, False), (8, 1, False)])
def test_m79_uses_existing_precision_and_support_bars(tp: int, fp: int, trusted: bool) -> None:
    assert real_games.Score(tp=tp, fp=fp).trusted is trusted


@pytest.mark.asyncio
async def test_m79_rejects_wrong_preregistration_before_measuring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "recorded.json"
    path.write_text(
        json.dumps(
            {
                "provenance": {"game_count": 80, "preregistration_commit": "not-registered"},
                "games": [{}] * 80,
            }
        )
    )
    monkeypatch.setattr(real_games, "M79_CORPUS", path)
    with pytest.raises(ValueError, match="preregistered ordinals"):
        await real_games.measure(path)


@pytest.mark.parametrize("capture_trusted,passes", [(True, True), (True, False), (False, False)])
def test_missed_capture_gate_uses_m79_not_development_corpora(
    monkeypatch: pytest.MonkeyPatch, capture_trusted: bool, passes: bool
) -> None:
    capture = real_games.MISSED_CAPTURE
    declared = trust.TRUSTED_CODES | {capture}
    if not capture_trusted:
        declared -= {capture}
    monkeypatch.setattr(trust, "TRUSTED_CODES", declared)
    monkeypatch.setattr("sys.argv", ["real_games", "--check"])

    async def measured(path: Path = real_games.CORPUS) -> dict[str, real_games.Score]:
        scores = {code: real_games.Score(tp=10) for code in declared | {capture}}
        scores[capture] = (
            real_games.Score(tp=8, fp=2)
            if path == real_games.M79_CORPUS and passes
            else real_games.Score(tp=7, fp=3)
        )
        return scores

    monkeypatch.setattr(real_games, "measure", measured)
    if capture_trusted and not passes:
        with pytest.raises(SystemExit, match="M79 fresh80 missed-capture"):
            real_games.main()
    else:
        real_games.main()
