"""The narrator benchmark runs without nvidia-smi; GPU fields become null (#389)."""

import subprocess
from typing import Any

import pytest

from benchmarks.narrator import run_benchmark


def _absent(*_args: Any, **_kwargs: Any) -> Any:
    raise FileNotFoundError("nvidia-smi")


def _failing(*_args: Any, **_kwargs: Any) -> Any:
    raise subprocess.CalledProcessError(9, "nvidia-smi")


@pytest.mark.parametrize("runner", [_absent, _failing])
def test_gpu_queries_return_none_without_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch, runner: Any
) -> None:
    monkeypatch.setattr(subprocess, "run", runner)
    assert run_benchmark._nvidia_used_mib() is None
    assert run_benchmark._gpu_name() is None


def test_gpu_memory_reads_first_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout="1214\n512\n")

    monkeypatch.setattr(subprocess, "run", fake)
    assert run_benchmark._nvidia_used_mib() == 1214


def test_gib_over_is_null_when_a_reading_is_missing() -> None:
    assert run_benchmark._gib_over(None, 100) is None
    assert run_benchmark._gib_over(2148, None) is None
    assert run_benchmark._gib_over(2148, 100) == 2.0
