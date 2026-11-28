"""``--resume`` appends only when the header still describes the code (#385, rule 6)."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from benchmarks.narrator.run_benchmark import (
    REPO_ROOT,
    RESUME_PINNED,
    load_config,
    narrator_digest,
    pipeline_identity,
    refuse_changed_setup,
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
    "narrator_sha256": "f" * 64,
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


@pytest.mark.parametrize("key", ["runner_sha256", "narrator_sha256", "config_sha256", "dirty"])
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


NARRATOR_MODULES = ("assembly", "slots", "facts", "factor_facts", "validator")


def test_header_digest_covers_every_narrator_module() -> None:
    identity = pipeline_identity(load_config(NARRATOR_TOML))
    modules = identity["narrator_modules"]
    for name in NARRATOR_MODULES:
        path = f"src/scan64/explanations/{name}.py"
        assert modules[path] == hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest()
    assert not [name for name in modules if "__pycache__" in name or name.endswith(".pyc")]
    assert identity["narrator_sha256"] == narrator_digest(REPO_ROOT / identity["module_file"])[0]


@pytest.mark.parametrize("module", NARRATOR_MODULES[1:])
def test_resume_refused_after_a_narrator_module_changed(tmp_path: Path, module: str) -> None:
    package = tmp_path / "explanations"
    package.mkdir()
    for name in NARRATOR_MODULES:
        (package / f"{name}.py").write_text(f"# {name}\n", encoding="utf-8")
    entry = package / "assembly.py"
    started, _ = narrator_digest(entry)
    header = {"pipeline": {**IDENTITY, "narrator_sha256": started}}
    refuse_resume(header, {**IDENTITY, "narrator_sha256": started})
    # assembly.py, the entry's own file and module_sha256, is unchanged.
    (package / f"{module}.py").write_text(f"# {module}, edited\n", encoding="utf-8")
    now, modules = narrator_digest(entry)
    assert now != started
    assert set(modules) == {f"explanations/{name}.py" for name in NARRATOR_MODULES}
    with pytest.raises(RuntimeError, match="narrator_sha256"):
        refuse_resume(header, {**IDENTITY, "narrator_sha256": now})


SETUP = {
    "commit": "abc",
    "gpu": "NVIDIA GeForce RTX 5090, 32607 MiB",
    "ollama_version": "0.40.18",
    "positions_sha256": "p" * 64,
    "num_ctx": 4096,
}


def _setup_header(**changes: object) -> dict[str, object]:
    fields = {**SETUP, **changes}
    return {"pipeline": {"commit": fields.pop("commit")}, **fields}


def test_resume_accepts_the_same_hardware_and_server() -> None:
    refuse_changed_setup(_setup_header(), dict(SETUP))


@pytest.mark.parametrize(
    ("key", "now"),
    [
        ("gpu", "NVIDIA GeForce RTX 4080 Laptop GPU, 12282 MiB"),
        ("ollama_version", "0.41.0"),
        ("positions_sha256", "q" * 64),
        ("num_ctx", 12288),
        ("commit", "def"),
    ],
)
def test_resume_refuses_other_hardware_server_positions_or_context(key: str, now: object) -> None:
    with pytest.raises(RuntimeError, match=f"Resume refused: changed .*{key}"):
        refuse_changed_setup(_setup_header(), {**SETUP, key: now})


def test_resume_refuses_a_header_without_the_setup_fields() -> None:
    header = {"pipeline": {"commit": "abc"}}
    with pytest.raises(RuntimeError, match="gpu"):
        refuse_changed_setup(header, dict(SETUP))
