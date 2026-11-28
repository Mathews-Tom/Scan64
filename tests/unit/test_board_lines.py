"""Analysis Board line facts, template and grounding (#368) on recorded Stockfish 18 lines."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import chess
import httpx
import pytest
from chess_lesson_spec import ExplanationClaim

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.board_lines import (
    BOARD_SCHEMA_PATH,
    COMPARISON_EVIDENCE_ID,
    BoardLinesFacts,
    board_lines_facts_schema,
    board_lines_grounding,
    board_lines_request,
    board_narrator_system_prompt,
    board_slot_catalogue,
    build_board_lines_facts,
    claim_line_id,
    numbered_san,
    resolve_board_lines_explanation,
    scored_legal_lines,
    template_board_claims,
    template_board_explanation,
)
from scan64.explanations.validator import (
    GroundedExplanationValidationError,
    validate_generated_explanation,
)
from scan64.providers.llm import ExplanationRequest, LLMProviderError, local_ollama
from scan64.providers.llm.config import MAX_CONCURRENT_CALLS_ENV
from scan64.providers.llm.contracts import GeneratedExplanation
from tests.fixtures.record_analysis_board_lines import engine_key

RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/analysis_board_lines.json").read_text(encoding="utf-8")
)
POSITIONS: dict[str, dict[str, Any]] = RECORDING["positions"]


def _facts(name: str, count: int = 3) -> BoardLinesFacts:
    entry = POSITIONS[name]
    board = chess.Board(entry["fen"])
    analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
    return build_board_lines_facts(board, scored_legal_lines(board, analysis)[:count])


def test_the_recording_names_the_engine_that_produced_every_line() -> None:
    engine = RECORDING["engine"]
    assert engine == engine_key(entry["config"] for entry in POSITIONS.values())
    # The UCI ``id name`` carries the engine's name and version.
    assert engine["engine_name"].startswith("Stockfish ")
    assert engine["engine_version"].startswith("sha256:")


def test_the_recorder_refuses_lines_from_more_than_one_engine() -> None:
    configs = [entry["config"] for entry in POSITIONS.values()]
    other = dict(configs[0], engine_version="sha256:" + "0" * 64)
    with pytest.raises(RuntimeError, match="found 2"):
        engine_key([*configs, other])


def test_stored_schema_matches_the_models() -> None:
    assert json.loads(BOARD_SCHEMA_PATH.read_text(encoding="utf-8")) == board_lines_facts_schema()


@pytest.mark.parametrize("name", sorted(POSITIONS))
@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_template_is_grounded_for_every_recorded_position(name: str, count: int) -> None:
    facts = _facts(name, count)
    explanation = template_board_explanation(facts)
    assert explanation.claims
    assert {claim.evidence_ref for claim in explanation.claims} >= {
        line.line_id for line in facts.lines
    }
    for claim in explanation.claims:
        assert claim_line_id(claim, facts) in {line.line_id for line in facts.lines}
    comparisons = [c for c in explanation.claims if c.evidence_ref == COMPARISON_EVIDENCE_ID]
    assert len(comparisons) == count - 1
    assert BoardLinesFacts.model_validate_json(facts.to_prompt_json(), strict=True) == facts


@pytest.mark.parametrize("name", sorted(POSITIONS))
def test_lines_keep_the_full_principal_variation(name: str) -> None:
    facts = _facts(name, 4)
    recorded = POSITIONS[name]["raw_result"]
    assert [list(line.uci) for line in facts.lines] == [entry["pv"] for entry in recorded]
    assert [len(line.moves) for line in facts.lines] == [len(entry["pv"]) for entry in recorded]
    for line, entry in zip(facts.lines, recorded, strict=True):
        assert line.depth == entry["depth"]
        assert line.moves[0].evaluation_after.status in ("centipawns", "mate")


def test_outcome_states_what_the_line_wins_and_threatens() -> None:
    facts = _facts("scholar_mate_in_one", 3)
    best = facts.lines[0]
    assert best.moves[0].san == "Qxf7#"
    assert best.evaluation.status == "mate"
    assert best.outcome.ends_in_checkmate
    assert best.outcome.material_change == 1
    assert best.outcome.checks_by_side_to_move == 1
    second = facts.lines[1]
    assert second.difference_from_best_cp is None  # mate against centipawns
    text = template_board_explanation(facts).text
    assert "Line 1: 4.Qxf7#." in text
    assert "mate in 1 for the side to move" in text
    assert "Line 1 ends in checkmate." in text


def test_comparison_states_the_score_difference() -> None:
    facts = _facts("start", 3)
    assert [line.difference_from_best_cp for line in facts.lines] == [None, 6, 11]
    text = template_board_explanation(facts).text
    assert "Line 2 (1.Nf3) scores 0.06 pawns less than line 1 for the side to move." in text


def test_numbered_san_follows_the_side_to_move() -> None:
    black = chess.Board(POSITIONS["italian_black"]["fen"])
    assert numbered_san(black, ["a7a5", "f3g5"]) == "8...a5 9.Ng5"
    assert numbered_san(chess.Board(), ["e2e4", "e7e5", "g1f3"]) == "1.e4 e5 2.Nf3"


def test_lines_with_an_illegal_or_unscored_move_are_dropped_whole() -> None:
    board = chess.Board()
    analysis = EngineAnalysis(
        raw_result=[
            {"pv": ["e2e4", "e2e4"], "score_cp": 30},
            {"pv": ["d2d4"], "score_cp": 20, "score_mate": 3},
            {"pv": ["0000"], "score_cp": 10},
            {"pv": ["g1f3", "g8f6"], "score_cp": 15},
        ]
    )
    assert [line.uci for line in scored_legal_lines(board, analysis)] == [("g1f3", "g8f6")]


def _claim(text: str, evidence_ref: str, line: tuple[str, ...]) -> ExplanationClaim:
    return ExplanationClaim(text=text, evidence_ref=evidence_ref, line=line, disclosure_level=1)


@pytest.mark.parametrize(
    ("claim", "reason"),
    [
        # A move of line 2 cited from line 1's evidence.
        (_claim("Line 1 starts with 1.Nf3.", "line-1", ("e2e4",)), "move_not_verified"),
        # An evaluation no line records.
        (_claim("Line 1 is worth +1.50.", "line-1", ("e2e4",)), "evaluation_not_supported"),
        # Line 1 cited under line 2.
        (_claim("Line 2 begins 1.Nf3.", "line-2", ("e2e4",)), "unverified_line"),
        # A difference the comparison does not hold.
        (
            _claim("Line 2 scores 0.90 pawns less than line 1.", COMPARISON_EVIDENCE_ID, ()),
            "evaluation_not_supported",
        ),
    ],
)
def test_validator_rejects_claims_the_lines_do_not_support(
    claim: ExplanationClaim, reason: str
) -> None:
    facts = _facts("start", 3)
    with pytest.raises(GroundedExplanationValidationError) as raised:
        validate_generated_explanation(
            GeneratedExplanation(claims=(claim,)), board_lines_grounding(facts)
        )
    assert raised.value.reason == reason


def test_claim_without_a_line_is_linked_to_none() -> None:
    facts = _facts("start", 3)
    with pytest.raises(GroundedExplanationValidationError) as raised:
        claim_line_id(_claim("The lines differ.", COMPARISON_EVIDENCE_ID, ()), facts)
    assert raised.value.reason == "missing_line"


class _Provider:
    def __init__(self, claims: tuple[ExplanationClaim, ...] | None) -> None:
        self.claims = claims
        self.requests: list[ExplanationRequest] = []

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        self.requests.append(request)
        if self.claims is None:
            raise LLMProviderError("provider unavailable")
        return GeneratedExplanation(claims=self.claims)


@pytest.mark.asyncio
async def test_without_a_provider_the_template_is_returned() -> None:
    facts = _facts("start", 3)
    resolved = await resolve_board_lines_explanation(facts, environment={})
    assert resolved.source == "template"
    assert resolved.fallback_reason is None
    assert resolved.explanation == template_board_explanation(facts)


@pytest.mark.asyncio
async def test_grounded_provider_wording_replaces_the_template() -> None:
    facts = _facts("start", 3)
    claims = (
        _claim(
            "The engine's first choice is {first:line-1}, scored {eval:line-1}.",
            "line-1",
            ("e2e4",),
        ),
        _claim(
            "{first:line-2} scores {diff:line-2}.",
            COMPARISON_EVIDENCE_ID,
            ("g1f3",),
        ),
    )
    provider = _Provider(claims)
    resolved = await resolve_board_lines_explanation(facts, local_provider=provider, environment={})
    assert resolved.source == "narrator"
    # Code fills every slot (#386); the claims keep their links.
    assert [claim.text for claim in resolved.explanation.claims] == [
        "The engine's first choice is 1.e4, scored +0.41 pawns.",
        "1.Nf3 scores 0.06 pawns less than line 1.",
    ]
    assert [claim.line for claim in resolved.explanation.claims] == [("e2e4",), ("g1f3",)]
    [request] = provider.requests
    # The model gets the slot list only (#408); the facts stay in the pipeline.
    assert [m.role for m in request.messages] == ["system", "user"]
    assert request.messages[1].content == board_slot_catalogue(facts).prompt()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ((_claim("Line 1 wins a queen.", "line-1", ("e2e4",)),), "chess_token_outside_slot"),
        ((_claim("{first:line-9} is best.", "line-1", ("e2e4",)),), "unknown_slot"),
        ((_claim("{first:line-2} is best.", "line-1", ("e2e4",)),), "slot_not_in_evidence"),
        ((_claim("The lines differ.", COMPARISON_EVIDENCE_ID, ()),), "missing_line"),
        (None, "provider_error"),
    ],
)
async def test_rejected_provider_output_falls_back_to_the_template(
    claims: tuple[ExplanationClaim, ...] | None, reason: str
) -> None:
    facts = _facts("start", 3)
    resolved = await resolve_board_lines_explanation(
        facts, local_provider=_Provider(claims), environment={}
    )
    assert resolved.source == "template"
    assert resolved.fallback_reason == reason
    assert resolved.explanation == template_board_explanation(facts)


def test_template_claims_cite_prefixes_of_their_lines() -> None:
    facts = _facts("king_pawn", 3)
    by_id = {line.line_id: line.uci for line in facts.lines}
    for claim in template_board_claims(facts).claims:
        linked = by_id[claim_line_id(claim, facts)]
        assert claim.line and linked[: len(claim.line)] == claim.line


def test_a_line_may_state_how_far_it_trails_line_one() -> None:
    facts = _facts("start", 3)
    claim = _claim("1.Nf3 is 6 centipawns worse than line 1.", "line-2", ("g1f3",))
    validate_generated_explanation(
        GeneratedExplanation(claims=(claim,)), board_lines_grounding(facts)
    )


@pytest.mark.parametrize("name", sorted(POSITIONS))
def test_prompt_is_the_slot_list_but_a_claim_may_cite_the_full_line(name: str) -> None:
    facts = _facts(name, 4)
    system, slots = board_lines_request(facts).messages
    assert system.content == board_narrator_system_prompt()
    catalogue = board_slot_catalogue(facts)
    assert slots.content == catalogue.prompt()
    # No facts JSON reaches the model (#408), so long lines do not grow the prompt (#388).
    assert '"schema_version"' not in system.content + slots.content
    for line in facts.lines:
        assert catalogue.slots_for(line.line_id)[f"line:{line.line_id}"].moves == line.uci
    # The facts the response, template and grounding use are not cut.
    assert [len(line.moves) for line in facts.lines] == [len(line.uci) for line in facts.lines]


@pytest.mark.asyncio
async def test_explanation_over_the_local_model_limit_gets_the_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two explanations at a limit of one: the second gets the template at once (#472)."""
    monkeypatch.setenv(MAX_CONCURRENT_CALLS_ENV, "1")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local"}]})
        entered.set()
        await release.wait()
        return httpx.Response(503)

    def transport(**_kwargs: object) -> httpx.MockTransport:
        return httpx.MockTransport(handler)

    monkeypatch.setattr(local_ollama.httpx, "AsyncHTTPTransport", transport)

    async def authorized() -> bool:
        return True

    facts = _facts("start", 3)

    async def explain() -> Any:
        provider = local_ollama.LocalOllamaExplanationProvider("local", authorized)
        return await resolve_board_lines_explanation(facts, local_provider=provider, environment={})

    holder = asyncio.create_task(explain())
    await entered.wait()
    over = await explain()
    assert over.source == "template"
    assert over.fallback_reason == "provider_error"
    assert over.explanation == template_board_explanation(facts)
    assert not holder.done()
    release.set()
    await holder
