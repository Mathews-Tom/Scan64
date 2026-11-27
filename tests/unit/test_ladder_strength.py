"""The ladder-strength harness: config validation, scoring and the Elo estimate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.engines.ladder_strength import (
    done_keys,
    elo_from,
    game_seed,
    ladder_score,
    load_config,
    manifest_id,
    summarise,
    wilson,
    write_or_check_manifest,
)
from scan64.providers.stockfish.identity import engine_identity

STRENGTH_TOML = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "engines" / "ladder_strength.toml"
)


def test_committed_config_loads_and_names_both_profiles() -> None:
    config = load_config(STRENGTH_TOML)
    assert config.ladder_profile.is_file() and config.reference_profile.is_file()
    assert len(config.anchors) == 8 and config.openings


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("anchors = [", "anchors = [1, "), "8 integers"),
        (('"e2e4 e7e5 g1f3 b8c6 f1b5 a7a6"', '"e2e4 e2e4"'), "illegal"),
        (("rounds = 2", "rounds = 0"), "run.rounds"),
    ],
)
def test_invalid_config_fails_fast(tmp_path: Path, change: tuple[str, str], message: str) -> None:
    path = tmp_path / "strength.toml"
    path.write_text(STRENGTH_TOML.read_text(encoding="utf-8").replace(*change, 1), "utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_score_is_from_the_ladder_side_and_skips_ply_limit() -> None:
    assert ladder_score({"result": "1-0", "ladder_color": "white"}) == 1.0
    assert ladder_score({"result": "1-0", "ladder_color": "black"}) == 0.0
    assert ladder_score({"result": "1/2-1/2", "ladder_color": "black"}) == 0.5
    assert ladder_score({"result": "*", "ladder_color": "white"}) is None
    assert elo_from(0.5, 1600) == 1600
    assert round(elo_from(0.75, 1600)) == 1791


def test_seed_is_stable_across_runs() -> None:
    assert game_seed((1, 0, "white", 0)) == game_seed((1, 0, "white", 0))
    assert game_seed((1, 0, "white", 0)) != game_seed((1, 0, "black", 0))


MANIFEST = {
    "code": {"commit": "a" * 40, "dirty": False},
    "config": {"path": "benchmarks/engines/ladder_strength.toml", "sha256": "c"},
    "ladder": {
        "profile": "ladder.toml",
        "profile_sha256": "l",
        "policy": "elo-v1",
        "sent_elo": {"3": 1003},
        "engine": {"engine_name": "Arasan", "engine_version": "sha256:x", "network": None},
    },
    "reference": {
        "profile": "ref.toml",
        "profile_sha256": "r",
        "engine": {"engine_name": "Stockfish 18", "engine_version": "sha256:y", "network": None},
    },
    "settings": {
        "reference_movetime_ms": 100,
        "anchors": [1600] * 8,
        "openings": ["e2e4"],
        "rounds": 1,
        "max_plies": 300,
    },
}


def _game(level: int, result: str, color: str, **extra: object) -> dict[str, object]:
    return {
        "run_id": manifest_id(MANIFEST),
        "level": level,
        "sent_elo": 1000 + level,
        "anchor": 1600,
        "opening": 0,
        "round": 0,
        "result": result,
        "ladder_color": color,
        **extra,
    }


def _write(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    results = tmp_path / "games.jsonl"
    write_or_check_manifest(results, MANIFEST)
    results.write_text("".join(json.dumps(record) + "\n" for record in records), "utf-8")
    return results


def test_summary_reports_estimate_and_unmeasurable_levels(tmp_path: Path) -> None:
    results = _write(
        tmp_path,
        [
            _game(3, "1-0", "white"),
            _game(3, "1/2-1/2", "black"),
            _game(3, "1-0", "black"),
            _game(3, "*", "white"),
            _game(4, "0-1", "white"),
            _game(5, "*", "white"),
        ],
    )
    text = summarise(results)
    assert "- Reference: Stockfish 18" in text
    assert "| 3 | 1003 | 1600 | 4 | 1-1-1 | 1 | 0.500 | 1600 (" in text
    assert "| 4 | 1004 | 1600 | 1 | 0-0-1 | 0 | 0.000 | below the measurable range" in text
    # No scored game: the score is unavailable, not zero.
    assert "| 5 | 1005 | 1600 | 1 | 0-0-0 | 1 | n/a | no scored games |" in text


def test_one_draw_has_a_wide_interval_not_a_point() -> None:
    low, high = wilson(0.5, 1)
    assert low < 0.2 and high > 0.8
    assert wilson(0.0, 3)[0] == 0.0


def test_summary_refuses_records_of_another_run(tmp_path: Path) -> None:
    results = _write(tmp_path, [_game(3, "1-0", "white"), _game(3, "1-0", "black", run_id="other")])
    with pytest.raises(ValueError, match="mixes records of runs"):
        summarise(results)


def test_summary_refuses_a_level_with_two_anchors(tmp_path: Path) -> None:
    results = _write(tmp_path, [_game(3, "1-0", "white"), _game(3, "1-0", "black", anchor=1320)])
    with pytest.raises(ValueError, match="disagree on sent Elo and anchor"):
        summarise(results)


def test_resume_refuses_a_changed_manifest(tmp_path: Path) -> None:
    results = _write(tmp_path, [_game(3, "1-0", "white")])
    assert done_keys(results, manifest_id(MANIFEST)) == {(3, 0, "white", 0)}
    changed = {**MANIFEST, "settings": {**MANIFEST["settings"], "anchors": [1320] * 8}}
    with pytest.raises(ValueError, match="Changed: settings"):
        write_or_check_manifest(results, changed)
    with pytest.raises(ValueError, match="holds a record of run"):
        done_keys(results, manifest_id(changed))


def test_records_without_a_manifest_are_refused(tmp_path: Path) -> None:
    results = tmp_path / "old.jsonl"
    results.write_text(json.dumps(_game(3, "1-0", "white")) + "\n", "utf-8")
    with pytest.raises(ValueError, match="no manifest"):
        write_or_check_manifest(results, MANIFEST)


def _arasan_identity(binary: Path, nnue: str) -> dict[str, object]:
    engine = SimpleNamespace(id={"name": "Arasan 26.1"}, config={"NNUE file": nnue})
    return engine_identity(engine, str(binary))  # type: ignore[arg-type]


def test_arasan_network_is_hashed_where_arasan_loads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "arasan"
    install.mkdir()
    binary = install / "arasanx-64"
    binary.write_bytes(b"arasan")
    network = install / "arasan.nnue"
    network.write_bytes(b"weights one")
    # A same-named file in the working directory is not the one Arasan loads.
    work = tmp_path / "work"
    work.mkdir()
    (work / "arasan.nnue").write_bytes(b"decoy")
    monkeypatch.chdir(work)

    first = _arasan_identity(binary, "arasan.nnue")
    assert first["network"] == json.dumps({"NNUE file": hashlib.sha256(b"weights one").hexdigest()})
    assert _arasan_identity(binary, str(network)) == first
    network.write_bytes(b"weights two")
    assert _arasan_identity(binary, "arasan.nnue")["network"] != first["network"]


def test_resume_refuses_a_changed_arasan_network(tmp_path: Path) -> None:
    install = tmp_path / "arasan"
    install.mkdir()
    binary = install / "arasanx-64"
    binary.write_bytes(b"arasan")
    network = install / "arasan.nnue"
    network.write_bytes(b"weights one")
    ladder = {**MANIFEST["ladder"], "engine": _arasan_identity(binary, "arasan.nnue")}
    results = tmp_path / "games.jsonl"
    write_or_check_manifest(results, {**MANIFEST, "ladder": ladder})
    network.write_bytes(b"weights two")
    changed = {**ladder, "engine": _arasan_identity(binary, "arasan.nnue")}
    with pytest.raises(ValueError, match="Changed: ladder"):
        write_or_check_manifest(results, {**MANIFEST, "ladder": changed})


@pytest.mark.parametrize("dirty_at", ["recorded", "current"])
def test_a_run_from_a_dirty_tree_cannot_be_resumed(tmp_path: Path, dirty_at: str) -> None:
    dirty = {**MANIFEST, "code": {"commit": "a" * 40, "dirty": True}}
    results = tmp_path / "games.jsonl"
    write_or_check_manifest(results, dirty if dirty_at == "recorded" else MANIFEST)
    with pytest.raises(ValueError, match="uncommitted changes"):
        write_or_check_manifest(results, MANIFEST if dirty_at == "recorded" else dirty)
