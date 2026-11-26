"""The narrator benchmark resolves its pipeline paths and scores a position (#369).

Nothing else in CI imports ``benchmarks/``; this catches a pipeline signature
change (such as #367's NarratorFacts) before an operator's GPU run does.
"""

import asyncio
from pathlib import Path
from typing import Any

from benchmarks.narrator.run_benchmark import (
    _resolve,
    load_config,
    load_positions,
)
from scan64.explanations.validator import GroundedExplanationContext

ROOT = Path(__file__).resolve().parents[2]


def test_configured_pipeline_paths_score_the_first_position() -> None:
    config = load_config(ROOT / "benchmarks/narrator/narrator.toml")
    position = load_positions(config.positions_file)[0]
    context_builder: Any = _resolve(config.grounding_context)
    entry: Any = _resolve(config.pipeline_entry)

    context = context_builder(position.diagnosis, list(position.evidence), position.fen)
    assert isinstance(context, GroundedExplanationContext)
    assert context.evidence

    # No provider: the pipeline returns the template floor without a model.
    explanation = asyncio.run(
        entry(
            position.diagnosis,
            list(position.evidence),
            position.fen,
            local_provider=None,
            environment={},
        )
    )
    assert explanation.text
