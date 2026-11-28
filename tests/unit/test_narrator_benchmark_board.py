"""The narrator benchmark measures the Analysis Board lines path (#449).

No model runs: the positions file rebuilds byte for byte, its facts equal the
endpoint's build from the recorded searches, the board entry points resolve
and return the template without a provider, and the summary scores board
records with the lesson metrics.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import chess
import pytest
from chess_lesson_spec import ExplanationClaim

from benchmarks.narrator.build_board_positions import BOARD_KIND, build_board_positions
from benchmarks.narrator.run_benchmark import (
    BOARD_CODE,
    REPO_ROOT,
    _claim_reason,
    _resolve,
    board_coverage,
    factor_positions,
    for_positions,
    load_any_positions,
    load_board_positions,
    load_config,
    load_positions,
    pipeline_identity,
    positions_kind,
    summarize,
    summary_markdown,
)
from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.board_lines import (
    board_line_positions,
    board_slot_grounding,
    build_board_lines_facts,
    claim_line_id,
    line_scores,
    scored_legal_lines,
)

NARRATOR_TOML = REPO_ROOT / "benchmarks/narrator/narrator.toml"
BOARD_FILE = REPO_ROOT / "benchmarks/narrator/positions-board.json"
LINES = REPO_ROOT / "tests/fixtures/analysis_board_lines.json"
SEARCHES = REPO_ROOT / "tests/fixtures/analysis_board_line_evaluations.json"


def _board_config() -> Any:
    config = load_config(NARRATOR_TOML)
    return for_positions(replace(config, positions_file=BOARD_FILE))


def test_the_committed_board_file_rebuilds_byte_for_byte() -> None:
    document = build_board_positions(LINES, SEARCHES, 3)
    rebuilt = json.dumps(document, indent=1, sort_keys=True) + "\n"
    assert BOARD_FILE.read_text(encoding="utf-8") == rebuilt
    assert document["kind"] == BOARD_KIND
    assert len(document["positions"]) == 12


def test_board_facts_equal_the_endpoint_build_from_the_recorded_searches() -> None:
    recorded = json.loads(LINES.read_text(encoding="utf-8"))["positions"]
    searches = json.loads(SEARCHES.read_text(encoding="utf-8"))
    for position in load_board_positions(BOARD_FILE):
        entry = recorded[position.position_id]
        board = chess.Board(entry["fen"])
        lines = scored_legal_lines(
            board, EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
        )[:3]
        boards = board_line_positions(board, lines)
        by_moves = {
            tuple(item["moves"]): item["raw_result"]
            for item in searches["positions"][position.position_id]
        }
        analyses: list[EngineAnalysis | None] = [
            EngineAnalysis(
                config=searches["config"],
                raw_result=by_moves[tuple(m.uci() for m in b.move_stack)],
            )
            for b in boards
        ]
        assert position.facts == build_board_lines_facts(
            board, lines, line_scores(boards, analyses)
        )
        assert position.code == BOARD_CODE
        assert len(position.facts.lines) == 3


def test_a_board_file_selects_the_board_entry_points() -> None:
    lessons = load_config(NARRATOR_TOML)
    assert for_positions(lessons) == lessons
    assert positions_kind(lessons.positions_file) == "lessons"
    config = _board_config()
    assert (
        config.pipeline_entry == "scan64.explanations.board_lines:resolve_board_lines_explanation"
    )
    assert config.grounding_context == "scan64.explanations.board_lines:board_slot_grounding"
    identity = pipeline_identity(config)
    assert identity["pipeline_entry"] == config.pipeline_entry
    assert identity["module_file"] == "src/scan64/explanations/board_lines.py"
    with pytest.raises(ValueError, match="board-lines"):
        load_positions(BOARD_FILE)
    assert [p.position_id for p in load_any_positions(BOARD_FILE)] == [
        p.position_id for p in load_board_positions(BOARD_FILE)
    ]


def test_the_board_entry_returns_the_template_without_a_provider() -> None:
    config = _board_config()
    position = load_board_positions(BOARD_FILE)[0]
    entry: Any = _resolve(config.pipeline_entry)
    result = asyncio.run(
        entry(position.facts, local_provider=None, environment={}, voice=config.voice)
    )
    assert result.source == "template"
    assert result.fallback_reason is None
    context = _resolve(config.grounding_context)(position.facts)
    assert context.slots is not None


def test_a_board_claim_linked_to_no_line_is_rejected_alone() -> None:
    facts = load_board_positions(BOARD_FILE)[0].facts
    context = board_slot_grounding(facts)

    def linked(explanation: Any) -> None:
        for claim in explanation.claims:
            claim_line_id(claim, facts)

    unlinked = ExplanationClaim(
        text="The lines differ.", evidence_ref="comparison", disclosure_level=1
    )
    assert _claim_reason(unlinked, context, linked)[0] == "missing_line"
    good = ExplanationClaim(
        text="{label:line-1} starts with {first:line-1}.", evidence_ref="line-1", disclosure_level=1
    )
    assert _claim_reason(good, context, linked) == (None, None)


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["eval"], 0.0),
        (["label:line-1", "first:line-1"], 1 / 3),
        (["outcome:line-2", "capture:line-3:1", "factor:mobility:line-3"], 2 / 3),
        (["diff:line-2", "line:line-1", "move:line-3:2"], 1.0),
    ],
)
def test_board_coverage_counts_the_lines_the_slots_name(names: list[str], expected: float) -> None:
    slots = [{"name": name, "value": "x", "moves": []} for name in names]
    assert board_coverage(slots, 3) == pytest.approx(expected)


def test_board_coverage_ignores_slots_the_list_did_not_hold() -> None:
    assert board_coverage([{"name": "label:line-2", "value": None, "moves": []}], 3) == 0.0


def _record(position_id: str, outcome: str, slots: list[str]) -> dict[str, Any]:
    used = [{"name": name, "value": "x", "moves": []} for name in slots]
    claims: list[dict[str, Any]] = [
        {
            "text": "x",
            "rejection_reason": None if outcome == "accepted" else "chess_token_outside_slot",
            "san_tokens": [],
        }
    ]
    return {
        "record": "run",
        "model": "gemma4-e4b-q3km",
        "position_id": position_id,
        "code": BOARD_CODE,
        "run": 0,
        "outcome": outcome,
        "fallback_reason": None if outcome == "accepted" else "chess_token_outside_slot",
        "provider_error": None,
        "latency_s": 1.0,
        "prompt_eval_count": 900,
        "eval_count": 200,
        "claims": claims,
        "slots_used": used,
    }


def test_the_summary_scores_board_records_with_the_lesson_metrics() -> None:
    config = _board_config()
    header = {
        "record": "header",
        "pipeline": pipeline_identity(config),
        "positions": 12,
        "runs_per_position": 1,
        "gpu": None,
        "ollama_version": "0",
        "positions_file": "benchmarks/narrator/positions-board.json",
    }
    records = [
        header,
        {
            "record": "model_load",
            "model": "gemma4-e4b-q3km",
            "load_and_first_call_s": 1.0,
            "ollama_size_bytes": 2**30,
            "ollama_size_vram_bytes": 2**30,
            "gpu_loaded_used_mib": None,
            "gpu_idle_used_mib": None,
        },
        _record("start", "accepted", ["label:line-1", "outcome:line-2"]),
        _record("en_passant", "fallback", ["label:line-1"]),
    ]
    summary = summarize(records, config)
    row = summary["models"]["gemma4-e4b-q3km"]
    assert summary["positions_kind"] == BOARD_KIND
    assert row["grounding_pass_rate"] == 0.5
    assert row["coverage_accepted"] == pytest.approx(2 / 3)
    assert row["fallback_reasons"] == {"chess_token_outside_slot": 1}
    assert row["false_chess_statements_per_explanation"] == 0.5
    assert "Analysis Board lines" in summary_markdown(summary)
    # Lines 2 and later carry factor slots on 5 of the 12 positions.
    assert "start" in factor_positions(config)
