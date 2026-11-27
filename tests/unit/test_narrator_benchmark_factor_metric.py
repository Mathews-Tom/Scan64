"""The narrator benchmark counts accepted answers that name a factor change (#410)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchmarks.narrator.run_benchmark import (
    factor_metric,
    factor_positions,
    load_config,
    names_factor,
    summary_markdown,
)

ROOT = Path(__file__).resolve().parents[2]
KING_FACT = "recorded:https://lichess.org/15lzmn9d:7"


def _run(position_id: str, *slots: tuple[str, str | None]) -> dict[str, Any]:
    return {
        "position_id": position_id,
        "slots_used": [{"name": name, "value": value, "moves": []} for name, value in slots],
    }


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        (_run("p", ("played", "b5"), ("factor:king_safety:best", "after your move, ...")), True),
        (_run("p", ("played", "b5")), False),
        # A factor slot the slot list did not hold states nothing.
        (_run("p", ("factor:space:played", None)), False),
        ({"position_id": "p", "claims": []}, None),
    ],
)
def test_names_factor(run: dict[str, Any], expected: bool | None) -> None:
    assert names_factor(run) is expected


def test_factor_metric_over_accepted_and_where_available() -> None:
    runs = [
        _run("a", ("factor:king_safety:best", "v")),
        _run("a", ("played", "b5")),
        _run("b", ("played", "b5")),
        {"position_id": "c", "claims": []},  # pre-slot record: not counted
    ]
    metric = factor_metric(runs, frozenset({"a"}))
    assert metric == {"factor_named_accepted": 1 / 3, "factor_named_where_available": 1 / 2}
    assert factor_metric([], frozenset()) == {
        "factor_named_accepted": None,
        "factor_named_where_available": None,
    }


def test_factor_positions_follow_the_configured_pipeline() -> None:
    config = load_config(ROOT / "benchmarks/narrator/narrator.toml")
    found = factor_positions(config)
    assert KING_FACT in found
    # 33 of the 49 measured benchmark positions have a fitted factor change.
    assert len(found) == 33


def test_summary_table_has_the_factor_column() -> None:
    row = {
        "role": "general",
        "params_b": 4,
        "grounding_pass_rate": 0.5,
        "coverage_accepted": 0.5,
        "delivered_coverage": 0.25,
        "full_coverage_rate": 0.1,
        "factor_named_accepted": 0.4,
        "factor_named_where_available": 0.6,
        "provider_error_rate": 0.0,
        "claims_per_explanation": 2.0,
        "false_chess_statements_per_explanation": 0.0,
        "rejected_claims_per_explanation": 0.0,
        "san_legal_rate": None,
        "san_correct_rate": None,
        "san_tokens": 0,
        "latency_p50_s": 1.0,
        "latency_p95_s": 2.0,
        "within_production_limit_rate": 1.0,
        "vram_model_gib": None,
    }
    header = {
        "pipeline": {"pipeline_entry": "e", "commit": "c", "module_sha256": "0" * 64},
        "positions": 50,
        "runs_per_position": 3,
        "gpu": None,
        "ollama_version": "0",
    }
    text = summary_markdown({"header": header, "models": {"m": row}})
    assert "Names a factor (accepted / where available)" in text
    assert "| 40.0% / 60.0% |" in text
    table = [line for line in text.splitlines() if line.startswith("|")]
    assert len({line.count("|") for line in table}) == 1
