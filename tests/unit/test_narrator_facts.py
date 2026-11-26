"""Narrator prompt: precomputed, schema-stated chess facts per move (#367).

Evidence here comes from the real composer (``compose_candidate_evidence``)
and the seeded detectors, fed the recorded corpus positions, so every
diagnosis kind those fixtures produce is exercised end to end.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import chess
import pytest
from chess_lesson_spec import Diagnosis, ExplanationClaim

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations import assembly
from scan64.explanations.assembly import (
    NARRATOR_SYSTEM_PROMPT,
    resolve_explanation,
    template_fallback_counts,
)
from scan64.explanations.facts import (
    MAX_LINE_PLIES,
    SCHEMA_PATH,
    CentipawnEvaluation,
    MateEvaluation,
    NarratorFacts,
    NarratorFactsError,
    UnknownEvaluation,
    build_narrator_facts,
    narrator_facts_schema,
    verified_lines_by_evidence,
)
from scan64.explanations.validator import (
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    validate_generated_explanation,
)
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry
from scan64.providers.llm import ExplanationRequest, GeneratedExplanation

_FIXTURES = Path(__file__).parents[1] / "fixtures"


def _analysis(lines: list[dict[str, Any]], nodes: int, multipv: int) -> EngineAnalysis:
    return EngineAnalysis(
        position_id=uuid4(), config={"nodes": nodes, "multipv": multipv}, raw_result=lines
    )


def _compose(
    before: chess.Board,
    played_uci: str,
    fast: list[dict[str, Any]],
    reply: list[dict[str, Any]],
    focused: list[dict[str, Any]],
    mate_source: list[dict[str, Any]] | None = None,
    swing_cp: int = 300,
    initial: chess.Board | None = None,
) -> tuple[list[Evidence], str]:
    move = chess.Move.from_uci(played_uci)
    played_san = before.san(move)
    start = before if initial is None else initial
    replay = chess.Board(start.fen())
    history = []
    for earlier in before.move_stack:
        history.append(replay.san(earlier))
        replay.push(earlier)
    history.append(played_san)
    after = before.copy()
    after.push(move)
    evidence = compose_candidate_evidence(
        before_board=before,
        after_board=after,
        history_san=history,
        initial_fen=start.fen(),
        position_id="pos_fixture",
        fast_analysis=_analysis(fast, 10_000, 1),
        reply_analysis=_analysis(reply, 10_000, 1),
        focused_analysis=_analysis(focused, 1_000_000, 4),
        mate_source_analysis=None if mate_source is None else _analysis(mate_source, 1_000_000, 4),
        played_move=played_san,
        swing_cp=swing_cp,
    )
    return evidence, played_san


async def _diagnoses(evidence: list[Evidence], played: str, swing_cp: int) -> list[Diagnosis]:
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    opportunity = LearningOpportunity(
        opportunity_id="fixture",
        position_id="pos_fixture",
        player_id="fixture-player",
        played_move=played,
        engine_eval_before=0.0,
        engine_eval_after=-(swing_cp / 100.0),
    )
    diagnoses = []
    for name in registry.names(kind=PluginKind.PATTERN_DETECTOR):
        detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=name)
        assert isinstance(detector, PatternDetector)
        for candidate in await detector.detect(
            opportunity, evidence, PlayerContext(player_id="fixture-player")
        ):
            diagnoses.append(
                Diagnosis(
                    primary=candidate.skill_id,
                    confidence=candidate.confidence,
                    evidence_refs=candidate.evidence_ids,
                )
            )
    return diagnoses


def _corpus_cases() -> list[tuple[str, chess.Board, list[Evidence], str, int]]:
    cases = []
    for fixture in json.loads((_FIXTURES / "live_detector_corpus.json").read_text()):
        initial = chess.Board(fixture["initial_fen"])
        board = initial.copy()
        candidate = fixture["candidate"]
        for uci in fixture["moves"][: candidate["move_index"]]:
            board.push_uci(uci)
        played = fixture["moves"][candidate["move_index"]]
        evidence, san = _compose(
            board,
            played,
            [{"pv": candidate["fast_before_pv"], "score_cp": 0}],
            [{"pv": candidate["fast_after_pv"], "score_cp": -300}],
            [{"pv": line, "score_cp": -300} for line in candidate["focused_multipv"]],
            swing_cp=candidate["swing_cp"],
            initial=initial,
        )
        cases.append((fixture["id"], board, evidence, san, candidate["swing_cp"]))
    # The corpus fork PV stops at the forking move, which proves no gain; the
    # composer needs the full winning line, so complete it: Nf6 Kb8 Nxh5.
    fork = chess.Board("k5r1/8/8/7r/4N3/8/8/1K6 w - - 0 1")
    evidence, san = _compose(
        fork,
        "e4d2",
        [{"pv": ["e4f6", "a8b8", "f6h5"], "score_cp": 480}],
        [{"pv": ["a8b8"], "score_cp": 20}],
        [{"pv": ["a8b8"], "score_cp": 20}, {"pv": ["a8a7"], "score_cp": 25}],
        swing_cp=500,
    )
    cases.append(("knight-fork-winning-line", fork, evidence, san, 500))
    mates = json.loads((_FIXTURES / "allowed_mate.json").read_text())
    for position in mates["positions"]:
        board = chess.Board(position["fen"])
        evidence, san = _compose(
            board,
            position["move"],
            position["before"],
            position["after"],
            position.get("focused_after", position["after"]),
            mate_source=position["mate_source"],
            swing_cp=10_000,
        )
        cases.append((position["id"], board, evidence, san, 10_000))
    for index, case in enumerate(json.loads((_FIXTURES / "m94/synthetic_cases.json").read_text())):
        board = chess.Board(case["fen"])
        played = board.parse_san(case["played"]).uci()
        after = board.copy()
        after.push_uci(played)
        reply = next(iter(after.legal_moves)).uci()
        score: dict[str, Any] = (
            {"score_mate": case["mate"]} if case["mate"] is not None else {"score_cp": 500}
        )
        evidence, san = _compose(
            board,
            played,
            [{"pv": case["line"], **score}],
            [{"pv": [reply], "score_cp": -500}],
            [{"pv": [reply], "score_cp": -500}],
            swing_cp=500,
        )
        cases.append((f"m94-{index}-{case['code']}", board, evidence, san, 500))
    return cases


_CASES = _corpus_cases()


def test_stored_schema_matches_the_model() -> None:
    stored = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert stored == narrator_facts_schema(), (
        "schemas/narrator-facts.schema.json is stale: run "
        "`uv run python -m scan64.explanations.facts`"
    )


@pytest.mark.asyncio
async def test_prompt_is_schema_valid_json_for_every_fixture_diagnosis() -> None:
    seen: set[str] = set()
    for case_id, board, evidence, played, swing in _CASES:
        diagnoses = await _diagnoses(evidence, played, swing)
        for diagnosis in diagnoses:
            facts = build_narrator_facts(diagnosis, evidence, board.fen())
            request = assembly._explanation_request(facts)
            system, user = request.messages
            assert system.content == NARRATOR_SYSTEM_PROMPT
            parsed = NarratorFacts.model_validate_json(user.content, strict=True)
            assert parsed == facts, case_id
            assert user.content == facts.to_prompt_json()
            assert json.loads(user.content)["schema_version"] == "narrator-facts/1"
            assert parsed.position.mistake_class.code == diagnosis.primary
            assert parsed.position.played_move.san == played
            for line in parsed.lines:
                assert [move.uci for move in line.moves] == list(line.uci)
                assert len(line.uci) <= MAX_LINE_PLIES
            seen.add(diagnosis.primary)
    assert seen == set(SEED_CODES), set(SEED_CODES) - seen


# 1.e4 e5 2.d3 Qg5: White missed Bxg5 and played Nf3.
_FEN = "rnb1kbnr/pppp1ppp/8/4p1q1/4P3/3P4/PPP2PPP/RNBQKBNR w KQkq - 1 3"


def _missed_capture() -> tuple[Diagnosis, list[Evidence]]:
    board = chess.Board(_FEN)
    evidence, _ = _compose(
        board,
        "g1f3",
        [{"pv": ["c1g5", "g8f6", "g5f6"], "score_cp": 850}],
        [{"pv": ["g5g2"], "score_cp": 40}],
        [
            {"pv": ["g5g2", "h1g1"], "score_cp": 60},
            {"pv": ["g5g6"], "score_cp": -20},
        ],
        swing_cp=890,
    )
    capture = next(item for item in evidence if item.payload.get("missed_type") == "capture")
    diagnosis = Diagnosis(
        primary="threat_processing.missed_capture",
        confidence=1.0,
        evidence_refs=[capture.evidence_id],
    )
    return diagnosis, evidence


def test_position_facts_state_evaluations_and_mistake_class() -> None:
    diagnosis, evidence = _missed_capture()
    position = build_narrator_facts(diagnosis, evidence, _FEN).position

    assert position.side_to_move == "white"
    assert position.played_move.san == "Nf3"
    assert position.best_move is not None
    assert position.best_move.san == "Bxg5"
    assert position.best_move.captures is not None
    assert position.best_move.captures.piece == "queen"
    assert position.best_move.material_after.learner_advantage == 9
    assert position.evaluation_before == CentipawnEvaluation(
        status="centipawns", centipawns=850, source="fast_pass"
    )
    # The reply search scores the position after Nf3 from Black's side: -40 for White.
    assert position.evaluation_after == CentipawnEvaluation(
        status="centipawns", centipawns=-40, source="reply_pass"
    )
    assert position.evaluation_difference_cp == 890
    assert position.mistake_class.name == "Missed capture"


def test_every_move_carries_board_facts_and_stored_evaluation() -> None:
    diagnosis, evidence = _missed_capture()
    facts = build_narrator_facts(diagnosis, evidence, _FEN)
    by_uci = {line.uci: line for line in facts.lines}

    best = by_uci[("c1g5", "g8f6", "g5f6")]
    assert best.begins_with_played_move is False
    assert [move.san for move in best.moves] == ["Bxg5", "Nf6", "Bxf6"]
    assert [move.mover for move in best.moves] == ["learner", "opponent", "learner"]
    assert best.moves[0].evaluation_after.status == "centipawns"
    assert best.moves[1].evaluation_after == UnknownEvaluation(status="unknown")
    assert best.moves[2].captures is not None
    assert best.moves[2].captures.piece == "knight"

    # Focused MultiPV line 1 is a continuation: it starts with the learner's move.
    continuation = by_uci[("g1f3", "g5g2", "h1g1")]
    assert continuation.begins_with_played_move is True
    assert continuation.moves[1].san == "Qxg2"
    assert continuation.moves[1].captures is not None
    assert continuation.moves[1].evaluation_after == CentipawnEvaluation(
        status="centipawns", centipawns=-60, source="focused_pass"
    )
    rook = {(threat.square, threat.piece) for threat in continuation.moves[1].new_threats}
    assert ("h1", "rook") in rook


def test_check_mate_castling_promotion_and_en_passant_facts() -> None:
    board = chess.Board("r3k2r/P7/8/3pP3/8/8/8/R3K2R w KQkq d6 0 1")
    payload = {
        "played_move": "Kf1",
        "fast_line": ["e5d6", "e8c8", "a7a8q", "c8d7", "e1g1"],
    }
    evidence = Evidence(
        evidence_id="ev_moves",
        kind="fixture",
        position_id="pos",
        engine_analysis_id="ea",
        claim="fixture",
        payload=payload,
    )
    diagnosis = Diagnosis(
        primary="threat_processing.missed_check", confidence=1.0, evidence_refs=["ev_moves"]
    )
    facts = build_narrator_facts(diagnosis, [evidence], board.fen())
    moves = facts.lines[0].moves
    assert moves[0].captures is not None and moves[0].captures.square == "d5"
    assert moves[1].castling == "queenside"
    assert moves[2].promotion == "queen" and moves[2].check is True
    assert moves[4].castling == "kingside"
    assert all(move.evaluation_after.status == "unknown" for move in moves)
    assert facts.position.evaluation_before == UnknownEvaluation(status="unknown")
    assert facts.position.evaluation_difference_cp is None


def test_mate_scores_are_from_the_learner_view() -> None:
    position = json.loads((_FIXTURES / "allowed_mate.json").read_text())["positions"][0]
    board = chess.Board(position["fen"])
    evidence, _ = _compose(
        board,
        position["move"],
        position["before"],
        position["after"],
        position["after"],
        mate_source=position["mate_source"],
        swing_cp=10_000,
    )
    mate = next(item for item in evidence if item.payload.get("blunder_type") == "allowed_mate")
    diagnosis = Diagnosis(
        primary="threat_processing.allowed_mate", confidence=1.0, evidence_refs=[mate.evidence_id]
    )
    facts = build_narrator_facts(diagnosis, evidence, board.fen())
    assert facts.position.evaluation_after == MateEvaluation(
        status="mate", mate_in=-1, source="reply_pass"
    )
    mating = next(line for line in facts.lines if "mating_line" in line.origins)
    assert mating.moves[-1].checkmate is True


def test_long_lines_are_cut_and_marked() -> None:
    board = chess.Board()
    shuffle = ["g1f3", "g8f6", "f3g1", "f6g8"] * 4
    evidence = Evidence(
        evidence_id="ev_long",
        kind="fixture",
        position_id="pos",
        engine_analysis_id="ea",
        claim="fixture",
        payload={"played_move": "a2a3", "fast_line": shuffle},
    )
    diagnosis = Diagnosis(
        primary="threat_processing.missed_check", confidence=1.0, evidence_refs=["ev_long"]
    )
    facts = build_narrator_facts(diagnosis, [evidence], board.fen())
    assert facts.lines[0].uci == tuple(shuffle[:MAX_LINE_PLIES])
    assert facts.lines[0].full_plies == len(shuffle)
    assert verified_lines_by_evidence(facts) == {"ev_long": (tuple(shuffle[:MAX_LINE_PLIES]),)}


def test_illegal_stored_line_raises() -> None:
    evidence = Evidence(
        evidence_id="ev_bad",
        kind="fixture",
        position_id="pos",
        engine_analysis_id="ea",
        claim="fixture",
        payload={"played_move": "e2e4", "fast_line": ["e7e5"]},
    )
    diagnosis = Diagnosis(
        primary="threat_processing.missed_check", confidence=1.0, evidence_refs=["ev_bad"]
    )
    with pytest.raises(NarratorFactsError, match="illegal"):
        build_narrator_facts(diagnosis, [evidence], chess.STARTING_FEN)


class _Provider:
    def __init__(self, text: str, line: tuple[str, ...] = ()) -> None:
        self.text = text
        self.line = line
        self.requests: list[ExplanationRequest] = []

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        self.requests.append(request)
        facts = json.loads(request.messages[1].content)
        return GeneratedExplanation(
            claims=(
                ExplanationClaim(
                    text=self.text,
                    evidence_ref=facts["evidence"][0]["evidence_id"],
                    line=self.line,
                    disclosure_level=1,
                ),
            )
        )


@pytest.mark.asyncio
async def test_resolve_sends_json_facts_and_accepts_a_continuation_line() -> None:
    diagnosis, evidence = _missed_capture()
    provider = _Provider(
        "After Nf3 the queen takes on g2 and attacks the rook on h1.",
        line=("g1f3", "g5g2"),
    )
    explanation = await resolve_explanation(
        diagnosis, evidence, _FEN, local_provider=provider, environment={}
    )
    assert explanation.claims[0].line == ("g1f3", "g5g2")
    NarratorFacts.model_validate_json(provider.requests[0].messages[1].content)


@pytest.mark.asyncio
async def test_illegal_evidence_falls_back_to_the_template_with_a_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    diagnosis, evidence = _missed_capture()
    broken = [
        item.model_copy(update={"payload": {**item.payload, "focused_line": ["e1e8"]}})
        for item in evidence
    ]
    template = await resolve_explanation(diagnosis, broken, _FEN, environment={})
    before = template_fallback_counts().get("invalid_evidence", 0)
    provider = _Provider("Bxg5 wins the queen.")
    with caplog.at_level(logging.INFO, logger=assembly.__name__):
        explanation = await resolve_explanation(
            diagnosis, broken, _FEN, local_provider=provider, environment={}
        )
    assert explanation == template
    assert provider.requests == []
    assert template_fallback_counts()["invalid_evidence"] == before + 1


def _context() -> tuple[GroundedExplanationContext, str]:
    diagnosis, evidence = _missed_capture()
    facts = build_narrator_facts(diagnosis, evidence, _FEN)
    context = assembly._grounding_context(facts, evidence, _FEN)
    return context, facts.evidence[0].evidence_id


def _check(text: str, line: tuple[str, ...] = ()) -> None:
    context, evidence_id = _context()
    validate_generated_explanation(
        GeneratedExplanation(
            claims=(
                ExplanationClaim(
                    text=text, evidence_ref=evidence_id, line=line, disclosure_level=1
                ),
            )
        ),
        context,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Before your move the engine rated the position 8.5 pawns in your favour.",
        "After Nf3 the evaluation is -0.4 for you.",
        "Nf3 cost 8.9 pawns compared with Bxg5.",
        "After Bxg5 you are up a queen.",
        "After Bxg5 you would be up 9 points of material.",
        "Material is equal before the capture.",
        "After Nf3 the queen takes on g2 and attacks the rook on h1.",
        "Qxg2 attacks the undefended rook on h1.",
        "Your bishop on c1 attacks the queen on g5.",
        "After Bxg5 Nf6 Bxf6, Black is down a queen and a knight in material.",
        "Your opponent is behind in material after Bxg5.",
    ],
)
def test_claims_using_the_new_facts_pass(text: str) -> None:
    _check(text)


def test_continuation_line_is_citable() -> None:
    _check("After Nf3, Qxg2 follows.", line=("g1f3", "g5g2", "h1g1"))


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("After Nf3 you are up two pawns.", "material_not_supported"),
        ("Black is ahead by a rook.", "material_not_supported"),
        ("You are behind by a queen.", "material_not_supported"),
        ("Nf3 attacks the black rook on h8.", "threat_not_supported"),
        ("The bishop attacks the undefended knight on g8.", "threat_not_supported"),
        ("After Nf3 the evaluation is 3.1 pawns.", "evaluation_not_supported"),
        ("Bxg5 forces mate in 3.", "mate_not_supported"),
    ],
)
def test_claims_beyond_the_facts_are_rejected(text: str, reason: str) -> None:
    with pytest.raises(GroundedExplanationValidationError) as raised:
        _check(text)
    assert raised.value.reason == reason


def test_line_from_the_after_position_without_the_played_move_is_rejected() -> None:
    with pytest.raises(GroundedExplanationValidationError) as raised:
        _check("Qxg2 follows.", line=("g5g2",))
    assert raised.value.reason == "illegal_line_move"
