"""``--resume`` appends only when the header still describes the code (#385, rule 6)."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from benchmarks.narrator.run_benchmark import (
    REPO_ROOT,
    RESUME_PINNED,
    load_config,
    pipeline_identity,
    refuse_resume,
)

NARRATOR_TOML = REPO_ROOT / "benchmarks" / "narrator" / "narrator.toml"

IDENTITY: dict[str, Any] = {
    "pipeline_entry": "scan64.explanations.assembly:resolve_explanation",
    "grounding_context": "scan64.explanations.assembly:grounding_context_for",
    "commit": "a" * 40,
    "module_commit": "b" * 40,
    "dirty": False,
    "module_file": "src/scan64/explanations/assembly.py",
    "module_sha256": "c" * 64,
    "runner_sha256": "d" * 64,
    "config_file": "benchmarks/narrator/narrator.toml",
    "config_sha256": "e" * 64,
}


def test_resume_accepted_for_the_same_clean_code() -> None:
    refuse_resume({"pipeline": dict(IDENTITY)}, dict(IDENTITY))


@pytest.mark.parametrize("key", RESUME_PINNED)
def test_resume_refused_when_a_pinned_field_changed(key: str) -> None:
    with pytest.raises(RuntimeError, match=key):
        refuse_resume({"pipeline": dict(IDENTITY)}, {**IDENTITY, key: "other"})


@pytest.mark.parametrize("key", ["runner_sha256", "config_sha256", "dirty"])
def test_resume_refused_for_a_header_from_an_older_runner(key: str) -> None:
    header = {k: v for k, v in IDENTITY.items() if k != key}
    with pytest.raises(RuntimeError, match="Resume refused"):
        refuse_resume({"pipeline": header}, dict(IDENTITY))


def test_resume_refused_from_a_dirty_tree() -> None:
    with pytest.raises(RuntimeError, match="uncommitted"):
        refuse_resume({"pipeline": dict(IDENTITY)}, {**IDENTITY, "dirty": True})


def test_resume_refused_for_a_run_started_dirty() -> None:
    with pytest.raises(RuntimeError, match="dirty"):
        refuse_resume({"pipeline": {**IDENTITY, "dirty": True}}, dict(IDENTITY))


def test_header_records_the_digest_of_the_config_file() -> None:
    identity = pipeline_identity(load_config(NARRATOR_TOML))
    assert identity["config_file"] == "benchmarks/narrator/narrator.toml"
    assert identity["config_sha256"] == hashlib.sha256(NARRATOR_TOML.read_bytes()).hexdigest()


def test_resume_refused_after_the_config_file_changed(tmp_path: Path) -> None:
    config_file = tmp_path / "narrator-local.toml"
    config_file.write_bytes(NARRATOR_TOML.read_bytes())
    started = pipeline_identity(load_config(config_file))
    # A --config file outside the repository is named without its directory.
    assert started["config_file"] == "narrator-local.toml"
    header = {"pipeline": {**started, "dirty": False}}
    refuse_resume(header, {**started, "dirty": False})
    config_file.write_bytes(NARRATOR_TOML.read_bytes() + b"\n# edited\n")
    now = pipeline_identity(load_config(config_file))
    assert now["config_sha256"] != started["config_sha256"]
    with pytest.raises(RuntimeError, match="config_sha256"):
        refuse_resume(header, {**now, "dirty": False})
