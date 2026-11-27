"""The compat runner records every feature as works/unavailable, never error."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.engines.compat import (
    FEATURES,
    CompatConfig,
    EngineEntry,
    file_sha256,
    load_config,
    run_engine,
    summarise,
)
from scan64.providers.engine.config import ENGINE_CONFIG_ENV
from tests.support.engines import fake_xboard_launcher

COMPAT_TOML = Path(__file__).resolve().parents[2] / "benchmarks" / "engines" / "compat.toml"


def test_committed_compat_set_loads_and_names_every_profile() -> None:
    config = load_config(COMPAT_TOML)
    assert len(config.engines) == 10
    assert all(entry.profile.is_file() for entry in config.engines)


@pytest.mark.asyncio
async def test_xboard_engine_run_records_capability_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, _ = fake_xboard_launcher(tmp_path, monkeypatch)
    profile = tmp_path / "fake.toml"
    profile.write_text(
        f'[engine]\nname = "fake"\nbinary_path = "{launcher.as_posix()}"\nprotocol = "xboard"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, "")
    entry = EngineEntry("Fake XBoard", profile, "test", "fixture", "XBoard only")
    base = load_config(COMPAT_TOML)
    config = CompatConfig(**{**base.__dict__, "results_dir": tmp_path, "engines": (entry,)})
    results = tmp_path / "compat.jsonl"
    await run_engine(entry, config, results, set())
    records = {
        record["feature"]: record
        for record in map(json.loads, results.read_text(encoding="utf-8").splitlines())
    }
    assert set(records) == set(FEATURES)
    assert records["game_analysis"]["status"] == "works"
    assert records["game_analysis"]["protocol"] == "xboard"
    provenance = records["game_analysis"]
    assert len(provenance["commit"]) == 40 and isinstance(provenance["dirty"], bool)
    assert provenance["binary_sha256"] == file_sha256(launcher)
    assert provenance["profile_sha256"] == file_sha256(profile)
    for name in FEATURES[1:]:
        assert records[name]["status"] == "unavailable", records[name]
        assert "FakeXBoard 1" in records[name]["reason"]
    table = summarise(results, config)
    assert "| works | unavailable |" in table
    assert "error" not in table


def test_results_written_under_benchmarks_do_not_make_the_tree_dirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmarks.engines.compat as compat

    calls: list[tuple[str, ...]] = []

    def git(*args: str) -> str:
        calls.append(args)
        return "" if args[0] == "status" else "0" * 40

    monkeypatch.setattr(compat, "_git", git)
    results = compat.REPO_ROOT / "benchmarks" / "engines" / "results"
    assert compat.code_provenance(results) == {"commit": "0" * 40, "dirty": False}
    status = next(call for call in calls if call[0] == "status")
    assert status[-1] == ":(exclude)benchmarks/engines/results"
    calls.clear()
    compat.code_provenance(Path("/outside/the/repo"))
    assert not any("exclude" in arg for call in calls for arg in call)
