from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import chess
import pytest
from chess_lesson_spec import Diagnosis

from scan64.chess.analysis.models import EngineAnalysis
from scan64.content.puzzle_practice import THEME_SKILLS
from scan64.explanations.templates import provider
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.trust import HISTORICALLY_TRUSTED_CODES, TRUSTED_CODES
from scan64.learning.evaluation.m94_reference import reference_labels
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION, compose_candidate_evidence
from scan64.learning.evidence.models import Evidence
from scan64.learning.evidence.motifs import CAPABILITIES, motif_payloads
from scan64.learning.evidence.support import supports_saved_diagnosis
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry

CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parents[1] / "fixtures/m94/synthetic_cases.json").read_text()
)


def _position(case: dict[str, Any], mirror: bool = False) -> tuple[chess.Board, list[str], str]:
    board = chess.Board(case["fen"])
    line = list(case["line"])
    played = board.parse_san(case["played"])
    if mirror:
        board = board.mirror()
        line = [
            chess.Move(
                chess.square_mirror(move.from_square), chess.square_mirror(move.to_square)
            ).uci()
            for move in (chess.Move.from_uci(uci) for uci in line)
        ]
        played = chess.Move(
            chess.square_mirror(played.from_square), chess.square_mirror(played.to_square)
        )
    assert board.is_valid()
    return board, line, board.san(played)


def _evidence(
    board: chess.Board, line: list[str], played: str, mate: object, swing: int = 200
) -> list[Evidence]:
    after = board.copy()
    after.push_san(played)
    return compose_candidate_evidence(
        before_board=board,
        after_board=after,
        history_san=[played],
        initial_fen=board.fen(),
        position_id="synthetic",
        fast_analysis=EngineAnalysis(
            config={}, raw_result=[{"pv": line, "score_cp": 900, "score_mate": mate}]
        ),
        reply_analysis=EngineAnalysis(config={}, raw_result=[]),
        focused_analysis=EngineAnalysis(config={"nodes": 1_000_000}, raw_result=[]),
        mate_source_analysis=None,
        played_move=played,
        swing_cp=swing,
    )


def _detector(code: str) -> PatternDetector:
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=code)
    assert isinstance(detector, PatternDetector)
    return detector


def _opportunity(played: str, before_cp: int = 0, after_cp: int = -200) -> LearningOpportunity:
    return LearningOpportunity(
        opportunity_id="synthetic",
        position_id="synthetic",
        player_id="synthetic",
        played_move=played,
        engine_eval_before=before_cp / 100.0,
        engine_eval_after=after_cp / 100.0,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["code"]))
@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.asyncio
async def test_source_proof_detector_oracle_and_ready_template(
    case: dict[str, Any], mirror: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, line, played = _position(case, mirror)
    evidence = _evidence(board, line, played, case["mate"])
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=case["code"])
    assert isinstance(detector, PatternDetector)
    opportunity = LearningOpportunity(
        opportunity_id="synthetic",
        position_id="synthetic",
        player_id="synthetic",
        played_move=played,
        engine_eval_before=0,
        engine_eval_after=-2,
    )
    candidates = await detector.detect(opportunity, evidence, PlayerContext(player_id="synthetic"))
    assert len(candidates) == 1
    selected = arbitrate_diagnoses(candidates, evidence, trusted_only=False)
    assert selected is not None and selected[0].skill_id == case["code"]
    assert arbitrate_diagnoses(candidates, evidence) is None
    after = board.copy()
    after.push_san(played)
    assert case["code"] in reference_labels(
        board, after, [{"pv": line, "score_mate": case["mate"]}]
    )
    diagnosis = Diagnosis(
        primary=case["code"], confidence=1, evidence_refs=candidates[0].evidence_ids
    )
    with pytest.raises(provider.ExplanationTemplateError, match="Untrusted"):
        await provider.TemplateExplanationProvider().explain(diagnosis, evidence)
    # Template readiness, not product promotion. Restore trust after this test.
    monkeypatch.setattr(provider, "TRUSTED_CODES", TRUSTED_CODES | {case["code"]})
    text = (await provider.TemplateExplanationProvider().explain(diagnosis, evidence)).text
    assert line[0] in text and played in text
    matched = next(item for item in evidence if item.evidence_id == candidates[0].evidence_ids[0])
    matched.payload["fast_line"] = ["not-a-move"]
    assert not await detector.detect(opportunity, evidence, PlayerContext(player_id="synthetic"))
    with pytest.raises(provider.ExplanationTemplateError, match="does not prove"):
        await provider.TemplateExplanationProvider().explain(diagnosis, evidence)


@pytest.mark.parametrize("saved_played", ["Kh2", "g1h2", "Kh2#"])
@pytest.mark.asyncio
async def test_motif_template_renders_canonical_played_san(
    monkeypatch: pytest.MonkeyPatch, saved_played: str
) -> None:
    case = next(item for item in CASES if item["code"] == "tactics.back_rank" and item["mate"] == 1)
    board, line, played = _position(case)
    evidence = _evidence(board, line, played, case["mate"])
    matched = next(item for item in evidence if item.payload.get("tactic_type") == "back_rank")
    matched.payload["played_move"] = saved_played
    diagnosis = Diagnosis(primary=case["code"], confidence=1, evidence_refs=[matched.evidence_id])
    monkeypatch.setattr(provider, "TRUSTED_CODES", TRUSTED_CODES | {case["code"]})
    text = (await provider.TemplateExplanationProvider().explain(diagnosis, evidence)).text
    assert f"Instead of {played}, {line[0]}" in text


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["code"]))
def test_omission_threshold_and_whole_legal_pv_are_required(case: dict[str, Any]) -> None:
    board, line, played = _position(case)
    after = board.copy()
    after.push_san(played)
    code = case["code"]
    assert not any(
        item.payload.get("tactic_type") == code.removeprefix("tactics.")
        for item in _evidence(board, line, played, case["mate"], 199)
    )
    root_san = board.san(chess.Move.from_uci(line[0]))
    root_after = board.copy()
    root_after.push_san(root_san)
    assert not any(
        item.payload.get("tactic_type") == code.removeprefix("tactics.")
        for item in _evidence(board, line, root_san, case["mate"])
    )
    assert code not in reference_labels(
        board, root_after, [{"pv": line, "score_mate": case["mate"]}]
    )
    for invalid in ([], ["invalid"], [*line, "a1a1"], [*line, "a1h8"]):
        assert not motif_payloads(board, invalid, case["mate"])
        assert not reference_labels(board, after, [{"pv": invalid, "score_mate": case["mate"]}])
    # No fast labels, payload markers or a later MultiPV line can fill missing deep evidence.
    assert not reference_labels(board, after, [])
    assert not reference_labels(
        board, after, [{"pv": []}, {"pv": line, "score_mate": case["mate"]}]
    )


@pytest.mark.parametrize("code", sorted(CAPABILITIES))
def test_per_code_capability_is_exact_and_not_global_trust(code: str) -> None:
    assert supports_saved_diagnosis(code, {"diagnosis_capabilities": dict(CAPABILITIES)})
    assert not supports_saved_diagnosis(code, {"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION})
    for markers in (
        None,
        [],
        "m94",
        {code: "wrong"},
        {code: CAPABILITIES[code].removesuffix("-v2") + "-v1"},
        {key: value for key, value in CAPABILITIES.items() if key != code},
    ):
        assert not supports_saved_diagnosis(code, {"diagnosis_capabilities": markers})
    assert code not in TRUSTED_CODES | HISTORICALLY_TRUSTED_CODES
    assert code not in {skill for skills in THEME_SKILLS.values() for skill in skills}
    assert not supports_saved_diagnosis(
        "tactics.unknown", {"diagnosis_capabilities": dict(CAPABILITIES)}
    )


def test_capabilities_cover_negative_saved_candidate_inputs_too() -> None:
    board, _, played = _position(CASES[0])
    evidence = _evidence(board, [], played, None)
    assert (
        evidence[0].payload["diagnosis_rule_version"]
        == DIAGNOSIS_RULE_VERSION
        == "m48-fast-local-v1"
    )
    assert all(supports_saved_diagnosis(code, evidence[0].payload) for code in CAPABILITIES)
    assert not any(
        item.payload.get("tactic_type") in {"skewer", "discovered_attack", "back_rank"}
        for item in evidence
    )


@pytest.mark.parametrize(
    "fen,line,code",
    [
        # Full PV gains a remote queen, but the skewered rook is an even exchange.
        (
            "4k2r/7r/8/8/8/1q6/2B5/R2K4 w - - 0 1",
            ["a1a8", "e8e7", "a8h8", "h7h8", "c2b3"],
            "tactics.skewer",
        ),
        # Already established skewer: the rook was attacking the front queen already.
        ("R3q2r/7k/8/8/8/8/8/1K6 w - - 0 1", ["a8b8", "e8e7", "b8h8"], "tactics.skewer"),
        # A bishop move does not discover a rook ray which was open already.
        (
            "q5k1/8/8/8/8/8/1B6/R5K1 w - - 0 1",
            ["b2c3", "g8h7", "a1a8"],
            "tactics.discovered_attack",
        ),
        # Target pawn is outside the preregistered material-motif definition.
        ("8/6k1/p7/8/8/8/B7/R5K1 w - - 0 1", ["a2c4", "g7h7", "a1a6"], "tactics.discovered_attack"),
        # Missing g7 blocker leaves a legal forward flight: Re8+ is not mate.
        ("6k1/5p1p/8/8/8/8/8/4R1K1 w - - 0 1", ["e1e8"], "tactics.back_rank"),
    ],
)
def test_geometry_and_remote_material_do_not_prove_a_motif(
    fen: str, line: list[str], code: str
) -> None:
    board = chess.Board(fen)
    assert board.is_valid()
    replay = board.copy()
    for uci in line:
        assert chess.Move.from_uci(uci) in replay.legal_moves
        replay.push_uci(uci)
    assert code not in {
        f"tactics.{payload['tactic_type']}" for payload in motif_payloads(board, line, 1)
    }
    assert code not in reference_labels(board, board, [{"pv": line, "score_mate": 1}])


@pytest.mark.parametrize("mate", [None, 0, -1, 2, 4, True, "1"])
def test_back_rank_requires_exact_scored_mate_distance(mate: object) -> None:
    board, line, _ = _position(CASES[2])
    assert not motif_payloads(board, line, mate)
    assert not reference_labels(board, board, [{"pv": line, "score_mate": mate}])


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["code"]))
@pytest.mark.parametrize(
    "before_cp,after_cp,expected",
    [
        (435, 235, True),
        (435, 236, False),
        (0, -200, True),
        (0, -199, False),
        (-235, -435, True),
        (-235, -434, False),
    ],
)
@pytest.mark.asyncio
async def test_detector_preserves_integer_cp_boundary(
    case: dict[str, Any], before_cp: int, after_cp: int, expected: bool
) -> None:
    board, line, played = _position(case)
    # Keep the source proof valid so the detector's own threshold is exercised.
    evidence = _evidence(board, line, played, case["mate"])
    candidates = await _detector(case["code"]).detect(
        _opportunity(played, before_cp, after_cp),
        evidence,
        PlayerContext(player_id="synthetic"),
    )
    assert [candidate.skill_id for candidate in candidates] == ([case["code"]] if expected else [])


@pytest.mark.parametrize(
    "case_index,changes",
    [
        pytest.param(2, {"mate_in": True}, id="boolean-mate-distance"),
        pytest.param(3, {"material_gain": False}, id="boolean-zero-material-gain"),
        pytest.param(2, {"source_score_mate": True}, id="boolean-source-mate"),
        pytest.param(2, {"best_move": "g1h2"}, id="best-move-not-pv-root"),
        pytest.param(2, {"played_move": "Re8#"}, id="saved-played-pv-root"),
        pytest.param(2, {"played_move": "--"}, id="saved-null-move"),
        pytest.param(2, {"played_move": "Kh3"}, id="saved-illegal-move"),
        pytest.param(2, {"played_move": True}, id="saved-non-string-move"),
        pytest.param(2, {"fast_line": ["e1e8", "0000"]}, id="null-pv-move"),
        pytest.param(
            2,
            {"diagnosis_capabilities": {"tactics.back_rank": "m94-back-rank-v1"}},
            id="archived-capability",
        ),
    ],
)
@pytest.mark.asyncio
async def test_saved_motif_proof_rejected_by_detector_and_renderer(
    case_index: int, changes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = CASES[case_index]
    board, line, played = _position(case)
    code = case["code"]
    evidence = _evidence(board, line, played, case["mate"])
    matched = next(
        item
        for item in evidence
        if item.payload.get("tactic_type") == code.removeprefix("tactics.")
    )
    matched.payload.update(changes)
    assert not await _detector(code).detect(
        _opportunity(played), evidence, PlayerContext(player_id="synthetic")
    )
    monkeypatch.setattr(provider, "TRUSTED_CODES", TRUSTED_CODES | {code})
    diagnosis = Diagnosis(primary=code, confidence=1, evidence_refs=[matched.evidence_id])
    with pytest.raises(provider.ExplanationTemplateError, match="does not prove"):
        await provider.TemplateExplanationProvider().explain(diagnosis, evidence)


@pytest.mark.parametrize(
    "saved_played,opportunity_played,expected",
    [
        ("Kh2", "g1h2", True),
        ("g1h2", "Kh2", True),
        ("Kh2", "Kf1", False),
        ("Kh2", "Re8#", False),
        ("Kh2", "--", False),
        ("Kh2", "Kh3", False),
    ],
)
@pytest.mark.asyncio
async def test_opportunity_and_saved_played_moves_match_by_move_not_notation(
    saved_played: str,
    opportunity_played: str,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = CASES[2]
    board, line, played = _position(case)
    code = case["code"]
    evidence = _evidence(board, line, played, case["mate"])
    matched = next(item for item in evidence if item.payload.get("tactic_type") == "back_rank")
    matched.payload["played_move"] = saved_played
    candidates = await _detector(code).detect(
        _opportunity(opportunity_played), evidence, PlayerContext(player_id="synthetic")
    )
    assert [candidate.skill_id for candidate in candidates] == ([code] if expected else [])
    # A different opportunity cannot invalidate this otherwise valid saved proof.
    monkeypatch.setattr(provider, "TRUSTED_CODES", TRUSTED_CODES | {code})
    diagnosis = Diagnosis(primary=code, confidence=1, evidence_refs=[matched.evidence_id])
    text = (await provider.TemplateExplanationProvider().explain(diagnosis, evidence)).text
    assert "e1e8 starts a back-rank mate in 1" in text


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["code"]))
def test_composer_rejects_null_played_move(case: dict[str, Any]) -> None:
    board, line, _ = _position(case)
    evidence = _evidence(board, line, "--", case["mate"])
    assert not any(
        item.payload.get("tactic_type") == case["code"].removeprefix("tactics.")
        for item in evidence
    )


@pytest.mark.asyncio
async def test_invalid_source_board_cannot_compose_detect_or_render_skewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = chess.Board("4q2r/8/8/8/8/8/8/R2K4 w - - 0 1")
    line = ["a1a8", "e8e7", "a8h8"]
    played = "Kc2"
    assert not board.is_valid()
    assert not motif_payloads(board, line, None)
    assert not any(
        item.payload.get("tactic_type") == "skewer" for item in _evidence(board, line, played, None)
    )
    # This formerly accepted proof names a queen-front skewer on a kingless board.
    saved = Evidence(
        evidence_id="invalid-source",
        kind="missed_tactic",
        position_id="synthetic",
        engine_analysis_id="synthetic",
        claim="a skewer needs a legal source position",
        payload={
            "diagnosis_capabilities": dict(CAPABILITIES),
            "before_fen": board.fen(),
            "fast_line": line,
            "best_move": line[0],
            "played_move": played,
            "source_score_mate": None,
            "tactic_type": "skewer",
            "front_square": "e8",
            "front_piece": "q",
            "target_square": "h8",
            "target_piece": "r",
            "slider_square": "a8",
            "material_gain": 5,
        },
    )
    assert not await _detector("tactics.skewer").detect(
        _opportunity(played), [saved], PlayerContext(player_id="synthetic")
    )
    monkeypatch.setattr(provider, "TRUSTED_CODES", TRUSTED_CODES | {"tactics.skewer"})
    diagnosis = Diagnosis(primary="tactics.skewer", confidence=1, evidence_refs=[saved.evidence_id])
    with pytest.raises(provider.ExplanationTemplateError, match="does not prove"):
        await provider.TemplateExplanationProvider().explain(diagnosis, [saved])
