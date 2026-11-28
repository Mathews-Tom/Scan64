"""The variant engine probe: fixed positions, undeclared variants and the PV legality check."""

from __future__ import annotations

import json
from pathlib import Path

import chess.variant
import pytest

from benchmarks.engines.variants_probe import (
    BOARDS,
    POSITIONS,
    ProbeConfig,
    final_raw_pvs,
    first_illegal_ply,
    load_config,
    manifest_path,
    run_probe,
    summarise,
    validate_positions,
)
from scan64.providers.engine.config import load_engine_profile
from tests.support.engines import fake_variant_uci_launcher

REPO_ROOT = Path(__file__).resolve().parents[2]
LINUX_CONFIG = REPO_ROOT / "benchmarks" / "engines" / "variants_probe_linux.toml"
STARTS = tuple(position for position in POSITIONS if position.label == "start")


def test_positions_cover_every_variant_with_fixed_non_trivial_positions() -> None:
    validate_positions(POSITIONS)
    assert set(BOARDS) == {
        "crazyhouse",
        "3check",
        "atomic",
        "antichess",
        "kingofthehill",
        "horde",
        "racingkings",
    }
    for variant in BOARDS:
        labels = [position.label for position in POSITIONS if position.variant == variant]
        assert labels[0] == "start" and 3 <= len(labels) <= 4, variant
    boards = [position.board() for position in POSITIONS]
    assert any(
        isinstance(board, chess.variant.CrazyhouseBoard)
        and any(len(pocket) for pocket in board.pockets)
        for board in boards
    )
    assert any(
        isinstance(board, chess.variant.ThreeCheckBoard) and min(board.remaining_checks) < 3
        for board in boards
    )


def test_committed_config_selects_a_linux_profile_without_uci_variant() -> None:
    config = load_config(LINUX_CONFIG)
    assert config.multipv == 3 and config.nodes > 0
    profile = load_engine_profile(config.profile)
    assert "UCI_Variant" not in profile.options and profile.threads == 1
    assert '"~/' in config.profile.read_text(encoding="utf-8")


def _config(tmp_path: Path, binary: Path) -> ProbeConfig:
    profile = tmp_path / "fake.toml"
    profile.write_text(f'[engine]\nname = "fake"\nbinary_path = "{binary.as_posix()}"\n', "utf-8")
    return ProbeConfig(path=profile, results_dir=tmp_path, profile=profile, nodes=1000, multipv=3)


def _records(results: Path) -> dict[str, dict[str, object]]:
    lines = results.read_text(encoding="utf-8").splitlines()
    return {record["variant"]: record for record in map(json.loads, lines)}


@pytest.mark.asyncio
async def test_undeclared_variants_are_recorded_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    none = fake_variant_uci_launcher(tmp_path, monkeypatch, "")
    results = tmp_path / "none.jsonl"
    await run_probe(_config(tmp_path, none), results, STARTS)
    records = _records(results)
    assert set(records) == set(BOARDS)
    for record in records.values():
        assert record["status"] == "not declared" and record["lines"] == []
        assert record["reason"] == "FakeVariant 1 declares no UCI_Variant option"
    manifest = json.loads(manifest_path(results).read_text(encoding="utf-8"))["manifest"]
    assert manifest["engine"]["declared_variants"] is None

    some = fake_variant_uci_launcher(tmp_path, monkeypatch, "chess,crazyhouse")
    results = tmp_path / "some.jsonl"
    await run_probe(_config(tmp_path, some), results, STARTS)
    records = _records(results)
    assert records["crazyhouse"]["status"] == "analysed"
    assert records["atomic"]["status"] == "not declared"
    assert records["atomic"]["reason"] == "FakeVariant 1 does not list atomic in UCI_Variant"
    assert "not declared: FakeVariant 1 does not list atomic" in summarise(results)


@pytest.mark.asyncio
async def test_pv_legality_is_checked_on_the_raw_engine_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = fake_variant_uci_launcher(tmp_path, monkeypatch, "crazyhouse,3check", illegal=True)
    results = tmp_path / "illegal.jsonl"
    await run_probe(_config(tmp_path, binary), results, STARTS[:2])
    record = _records(results)["crazyhouse"]
    assert record["status"] == "analysed" and record["lines_returned"] == 3
    lines = record["lines"]
    assert isinstance(lines, list)
    first, second, third = lines
    assert second["pv"] == ["a2a4", "a2a4"]
    assert second["legal"] is False and second["first_illegal_ply"] == 1
    # python-chess drops a pv it cannot play, so its parsed line differs.
    assert second["parsed_pv_matches"] is False
    assert first["legal"] and third["legal"] and first["parsed_pv_matches"]
    assert record["all_pv_legal"] is False

    # The summary re-checks legality from the recorded moves, not the stored flag.
    rows = results.read_text(encoding="utf-8").splitlines()
    tampered = [json.loads(row) for row in rows]
    for line in tampered[0]["lines"]:
        line["legal"] = True
    results.write_text("".join(json.dumps(row) + "\n" for row in tampered), "utf-8")
    summary = summarise(results)
    assert "| CrazyhouseBoard -> crazyhouse | start | 20 | analysed | 3 | 2, 2, 2 | no |" in summary
    assert "crazyhouse/start line 2" in summary


def test_variant_rules_decide_legality() -> None:
    antichess = chess.variant.AntichessBoard(
        "rnbqkbnr/p1pppppp/8/1p6/8/4P3/PPPP1PPP/RNBQKBNR w - - 0 2"
    )
    assert first_illegal_ply(antichess, ["f1b5"]) is None
    assert first_illegal_ply(antichess, ["e3e4"]) == 0  # capture is compulsory
    crazyhouse = chess.variant.CrazyhouseBoard(
        "r1bq1b1r/ppp2kpp/2n5/3np3/2B5/8/PPPP1PPP/RNBQK2R[PPnp] w KQ - 0 7"
    )
    assert first_illegal_ply(crazyhouse, ["d2d3", "N@e4"]) is None
    assert first_illegal_ply(crazyhouse, ["N@e6"]) == 0  # white holds no knight


def test_raw_pv_parser_skips_text_and_empty_pvs_are_not_legal() -> None:
    pvs = final_raw_pvs(
        [
            "info depth 1 string pv e2e4",
            "info depth 2 multipv 2 score cp 5 pv d2d4 d7d5 hashfull 3",
            "info depth 2 score cp 9 pv e2e4",
            "info depth 2 multipv 3 pv",
        ]
    )
    assert pvs == {1: ["e2e4"], 2: ["d2d4", "d7d5"], 3: []}
    assert first_illegal_ply(chess.variant.HordeBoard(), []) == 0


def test_summary_refuses_records_of_another_run(tmp_path: Path) -> None:
    results = tmp_path / "run.jsonl"
    manifest_path(results).write_text(json.dumps({"run_id": "a", "manifest": {}}), "utf-8")
    results.write_text(json.dumps({"run_id": "b"}) + "\n", "utf-8")
    with pytest.raises(ValueError, match="record of run b, not a"):
        summarise(results)
