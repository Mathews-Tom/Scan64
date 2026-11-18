from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import chess.engine
import chess.pgn
import pytest

from scan64.learning.diagnosis import trust
from scan64.learning.evaluation import real_games
from scan64.learning.evaluation.m93_protocol import CODES, PROTOCOL, validate_provenance
from scan64.learning.evaluation.real_games import (
    CORPUS,
    FRESH_CORPUS,
    M79_CORPUS,
    M93_CORPUS,
    M93_PREREGISTRATION,
    ORIGINAL_GATED_CODES,
    Score,
)
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.support import M48_SUPPORTED_CODES, supports_saved_diagnosis
from scripts import record_diagnosis_reference as recorder


@pytest.mark.parametrize("code", sorted(M48_SUPPORTED_CODES))
def test_saved_m48_capability_is_not_product_trust(code: str) -> None:
    assert supports_saved_diagnosis(code, {"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION})
    assert not supports_saved_diagnosis(code, {})
    assert not supports_saved_diagnosis(code, {"diagnosis_rule_version": "older"})


@pytest.mark.parametrize("code", ["unknown", "tactics.fork", "tactics.skewer", ""])
def test_unsupported_codes_never_inherit_m48_capability(code: str) -> None:
    assert not supports_saved_diagnosis(code, {"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION})


@pytest.mark.parametrize(
    "field", [*PROTOCOL, "protocol", "protocol_sha256", "preregistration_commit"]
)
@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_m93_rejects_altered_or_missing_provenance(field: str, mutation: str) -> None:
    corpus = json.loads(M93_CORPUS.read_text())
    validate_provenance(corpus, M93_PREREGISTRATION)
    altered = deepcopy(corpus)
    target = altered["provenance"]
    if field not in target:
        target = target["protocol"]
    if mutation == "missing":
        del target[field]
    else:
        target[field] = "changed"
    with pytest.raises(ValueError, match="provenance"):
        validate_provenance(altered, M93_PREREGISTRATION)


def test_m93_fixed_games_match_separate_qualified_pgn() -> None:
    corpus = json.loads(M93_CORPUS.read_text())
    pgn = M93_CORPUS.with_name("m93_fresh80_real_games.pgn")
    assert hashlib.sha256(pgn.read_bytes()).hexdigest() == corpus["provenance"]["pgn_sha256"]
    assert M93_CORPUS.stat().st_size <= 8_000_000
    with pgn.open() as stream:
        games = []
        while (game := chess.pgn.read_game(stream)) is not None:
            assert not game.errors
            assert all(600 <= int(game.headers[key]) <= 1600 for key in ("WhiteElo", "BlackElo"))
            assert len(list(game.mainline_moves())) >= 20
            games.append(game)
    assert len(games) == len(corpus["games"]) == 80
    assert [game.headers["Site"] for game in games] == [game["id"] for game in corpus["games"]]
    previous_ids = {
        game["id"]
        for path in (CORPUS, FRESH_CORPUS, M79_CORPUS)
        for game in json.loads(path.read_text())["games"]
    }
    assert not previous_ids & {game["id"] for game in corpus["games"]}
    for game in corpus["games"]:
        assert len(game["fast"]) == len(game["moves"]) + 1
        assert set(game["deep_before"]) == set(game["focused"])


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["recorded.json", "real_games.pgn"])
async def test_m93_recorder_refuses_either_existing_artifact_before_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    monkeypatch.setattr(recorder, "DEST", tmp_path)
    (tmp_path / f"m93_fresh80_{suffix}").write_text("consumed holdout")
    with pytest.raises(ValueError, match="overwrite"):
        await recorder.record(tmp_path / "not-even-a-source.zst", m93_fresh80=True)


@pytest.mark.asyncio
async def test_m93_tampering_fails_before_offline_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / M93_CORPUS.name
    corpus = json.loads(M93_CORPUS.read_text())
    corpus["games"][0]["moves"][0] = "altered"
    path.write_text(json.dumps(corpus))
    monkeypatch.setattr(real_games, "M93_CORPUS", path)
    with pytest.raises(ValueError, match="checksum"):
        await real_games.measure(path)


@pytest.mark.parametrize("failing_gate", ["original", "M93", "unknown", "none"])
def test_promoted_motifs_have_their_own_gate_not_original40(
    monkeypatch: pytest.MonkeyPatch, failing_gate: str
) -> None:
    motif = "tactics.fork.knight"

    async def measured(path: Path = CORPUS) -> dict[str, Score]:
        result = {code: Score(tp=10) for code in real_games.SEED_CODES}
        if path == CORPUS:
            result[motif] = Score(tp=0)
            if failing_gate == "original":
                result["board_awareness.hanging_piece"] = Score(tp=7, fp=3)
        if path == M93_CORPUS and failing_gate == "M93":
            result[motif] = Score(tp=7, fp=3)
        return result

    monkeypatch.setattr(real_games, "measure", measured)
    trusted = ORIGINAL_GATED_CODES | {motif}
    if failing_gate == "unknown":
        trusted |= {"unmeasured"}
    monkeypatch.setattr(trust, "TRUSTED_CODES", trusted)
    monkeypatch.setattr("sys.argv", ["real_games", "--check"])
    if failing_gate == "none":
        real_games.main()
    else:
        expected = {"original": "Original", "M93": "M93", "unknown": "no declared"}
        with pytest.raises(SystemExit, match=expected[failing_gate]):
            real_games.main()


@pytest.mark.asyncio
async def test_m93_replay_is_engine_free_and_promotions_match_verdicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scan64.content.puzzle_practice import THEME_SKILLS

    async def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Offline replay launched an engine")

    monkeypatch.setattr(chess.engine, "popen_uci", forbidden)
    scores = await real_games.measure(M93_CORPUS)
    passing = {code for code in CODES if scores[code].trusted}
    assert trust.TRUSTED_CODES & CODES == passing
    assert trust.HISTORICALLY_TRUSTED_CODES & CODES == passing
    mapped = {code for codes in THEME_SKILLS.values() for code in codes}
    assert mapped & CODES == passing
