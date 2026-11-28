"""The default narrator model is the file and Modelfile the #462 benchmark ran (#429, #462)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from scan64.providers.llm import local_ollama
from scan64.providers.llm.adapters import DEFAULT_OLLAMA_CONTEXT_TOKENS
from scan64.providers.llm.config import LLMProviderConfig
from scan64.providers.llm.narrator_model import (
    DEFAULT_NARRATOR_GGUF,
    DEFAULT_NARRATOR_MODEL,
    MODELFILE_TEMPLATE,
    NARRATOR_GPU_MEMORY_BUDGET_MIB,
    render_modelfile,
)

BENCHMARK = Path(__file__).parents[2] / "benchmarks/narrator"
BENCHMARK_NAME = "gemma4-e4b-q4ks"


def _benchmark_entry() -> dict[str, object]:
    config = tomllib.loads((BENCHMARK / "narrator.toml").read_text(encoding="utf-8"))
    (entry,) = [model for model in config["models"] if model["name"] == BENCHMARK_NAME]
    return entry


def test_the_pin_is_the_benchmarked_gguf() -> None:
    entry = _benchmark_entry()
    assert (
        entry["hf_repo"],
        entry["hf_revision"],
        entry["hf_file"],
        entry["sha256"],
        entry["licence"],
    ) == (
        DEFAULT_NARRATOR_GGUF.hf_repo,
        DEFAULT_NARRATOR_GGUF.hf_revision,
        DEFAULT_NARRATOR_GGUF.hf_file,
        DEFAULT_NARRATOR_GGUF.sha256,
        DEFAULT_NARRATOR_GGUF.licence,
    )
    assert DEFAULT_NARRATOR_GGUF.url == (
        "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/"
        "bfc15c382204943c3a8fff0c750b94ae2364d7a3/gemma-4-E4B-it-Q4_K_S.gguf"
    )


def test_the_modelfile_is_the_benchmark_modelfile() -> None:
    benchmark = (BENCHMARK / f"modelfiles/{BENCHMARK_NAME}.Modelfile").read_text(encoding="utf-8")
    assert MODELFILE_TEMPLATE == benchmark
    rendered = render_modelfile("./gemma-4-E4B-it-Q4_K_S.gguf")
    assert "FROM ./gemma-4-E4B-it-Q4_K_S.gguf\n" in rendered
    assert "PARAMETER num_ctx 4096\n" in rendered
    assert "PARAMETER num_predict 2048\n" in rendered
    assert "@" not in rendered


def test_the_default_context_is_4096_on_both_ollama_paths() -> None:
    assert DEFAULT_OLLAMA_CONTEXT_TOKENS == 4096
    assert local_ollama.LocalOllamaLimits.from_environment({}).context_tokens == 4096
    assert LLMProviderConfig(provider="ollama").context_tokens == 4096


def test_the_model_name_and_memory_budget() -> None:
    assert DEFAULT_NARRATOR_MODEL == "scan64-narrator-gemma4-e4b-q4ks:latest"
    assert NARRATOR_GPU_MEMORY_BUDGET_MIB == 4096
