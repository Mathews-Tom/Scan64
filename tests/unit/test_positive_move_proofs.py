from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import chess
import pytest

import scan64.chess.analysis.credit as credit
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.positions.models import Position
from scan64.learning.tolerance import win_probability

RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/m61_positive_proofs.json").read_text()
)
CASES: dict[str, dict[str, Any]] = RECORDING["cases"]


def _inputs(name: str) -> tuple[GameMoveReview, Position, EngineAnalysis, Position]:
    case = CASES[name]
    source = ingest_fen(case["fen"])
    predecessor = ingest_fen(case["predecessor_fen"])
    board = chess.Board(source.fen)
    analysis = EngineAnalysis(position_id=source.id, raw_result=copy.deepcopy(case["raw_result"]))
    move = board.parse_uci(analysis.raw_result[0]["pv"][0])
    probability = win_probability(analysis.raw_result[0])
    assert probability is not None
    # Isolate the proof policy with exactly sound, scored A8 inputs. The
    # tactical PV/score itself is the unchanged recorded engine output.
    row = GameMoveReview(
        game_id=uuid4(),
        ply=case["source_ply"],
        source_position_id=source.id,
        reached_position_id=uuid4(),
        before_analysis_id=analysis.id,
        after_analysis_id=uuid4(),
        move_uci=move.uci(),
        san=board.san(move),
        mover="white" if board.turn else "black",
        before_probability=probability,
        after_probability=1.0 - probability,
    )
    return row, source, analysis, predecessor


@pytest.mark.parametrize("name", CASES)
def test_recorded_conservative_tactical_proofs(name: str) -> None:
    row, source, analysis, predecessor = _inputs(name)
    case = CASES[name]
    proof, exclusion = credit.positive_move_proof(
        row,
        source,
        analysis,
        predecessor=predecessor,
        preceding_move_uci=case["preceding_move_uci"],
    )
    if "exclusion" in case:
        assert proof is None
        assert exclusion == case["exclusion"]
    else:
        assert exclusion is None
        assert proof is not None
        for key in ("skill_id", "material_gain", "exchange_gain", "net_material_gain"):
            if key in case:
                assert proof[key] == case[key]
        assert proof["line"] == case["raw_result"][0]["pv"]
        assert proof["source_analysis_id"] == str(analysis.id)
        assert proof["reached_analysis_id"] == str(row.after_analysis_id)
        assert proof["proof_version"] == 1
        assert proof["ply"] == row.ply
        assert proof["canonical_id"] == source.canonical_id
        assert proof["explanation"]
        assert proof["assistance"] == "no in-app hint"


def test_recorded_lines_are_verbatim_from_once_recorded_engine_corpus() -> None:
    corpus = json.loads((Path(__file__).parents[1] / "fixtures/m48/recorded.json").read_text())
    games = {game["id"]: game for game in corpus["games"]}
    for case in CASES.values():
        assert case["raw_result"] == games[case["source_game"]]["fast"][case["source_ply"] - 1]


@pytest.mark.parametrize("name", ["hung_white", "hung_black"])
def test_capture_requires_the_opponents_preceding_move(name: str) -> None:
    row, source, analysis, _ = _inputs(name)
    proof, exclusion = credit.positive_move_proof(row, source, analysis)
    assert proof is None
    assert exclusion == "missing opponent preceding move"


@pytest.mark.parametrize("line", [None, [], [123], ["a8g8", "a1a8"], ["not-uci"]])
def test_missing_malformed_or_illegal_lines_prove_nothing(line: Any) -> None:
    row, source, analysis, predecessor = _inputs("hung_white")
    analysis.raw_result = [{"pv": line, "score_cp": 978}]
    proof, exclusion = credit.positive_move_proof(
        row, source, analysis, predecessor=predecessor, preceding_move_uci="e8e7"
    )
    assert proof is None
    assert exclusion is not None


def test_truncated_check_does_not_prove_a_material_win() -> None:
    row, source, analysis, predecessor = _inputs("material_check")
    analysis.raw_result[0]["pv"] = analysis.raw_result[0]["pv"][:1]
    proof, exclusion = credit.positive_move_proof(row, source, analysis, predecessor=predecessor)
    assert proof is None
    assert exclusion == "illegal or truncated proving line"


def test_missing_score_or_unscored_a8_is_not_credit() -> None:
    row, source, analysis, predecessor = _inputs("hung_white")
    analysis.raw_result[0].pop("score_cp")
    assert credit.positive_move_proof(row, source, analysis, predecessor=predecessor)[0] is None
    row, source, analysis, predecessor = _inputs("hung_white")
    row.after_probability = None
    assert credit.positive_move_proof(row, source, analysis, predecessor=predecessor) == (
        None,
        "unscored move",
    )
    row.after_probability = 1.0
    assert credit.positive_move_proof(row, source, analysis, predecessor=predecessor) == (
        None,
        "move is not A8 sound",
    )


def test_a_legal_alternative_is_not_the_exact_first_pv_move() -> None:
    row, source, analysis, predecessor = _inputs("hung_white")
    row.move_uci = "a8a7"
    assert chess.Move.from_uci(row.move_uci) in chess.Board(source.fen).legal_moves
    assert credit.positive_move_proof(row, source, analysis, predecessor=predecessor) == (
        None,
        "not the first source PV move",
    )


def test_negative_owner_mate_score_is_not_a_winning_check() -> None:
    row, source, analysis, predecessor = _inputs("mate_check")
    analysis.raw_result[0]["score_mate"] = -3
    row.before_probability, row.after_probability = 0.0, 1.0
    assert credit.positive_move_proof(row, source, analysis, predecessor=predecessor) == (
        None,
        "check does not prove mate or a material gain",
    )


def test_epd_identity_ignores_counters_without_reusing_position_ids() -> None:
    row, source, analysis, predecessor = _inputs("hung_white")
    board = chess.Board(source.fen)
    board.halfmove_clock += 8
    board.fullmove_number += 4
    repeated = ingest_fen(board.fen())
    assert repeated.id != source.id
    assert repeated.fen != source.fen
    assert repeated.canonical_id == source.canonical_id
    previous_board = chess.Board(predecessor.fen)
    previous_board.halfmove_clock += 8
    previous_board.fullmove_number += 4
    repeated_predecessor = ingest_fen(previous_board.fen())
    row.source_position_id = repeated.id
    analysis.position_id = repeated.id
    proof, exclusion = credit.positive_move_proof(
        row,
        repeated,
        analysis,
        predecessor=repeated_predecessor,
        preceding_move_uci=CASES["hung_white"]["preceding_move_uci"],
    )
    assert exclusion is None
    assert proof is not None and proof["canonical_id"] == source.canonical_id


def test_positive_proof_still_requires_trusted_code(monkeypatch: pytest.MonkeyPatch) -> None:
    row, source, analysis, predecessor = _inputs("hung_white")
    monkeypatch.setattr(credit, "TRUSTED_CODES", frozenset())
    assert credit.positive_move_proof(
        row, source, analysis, predecessor=predecessor, preceding_move_uci="e8e7"
    ) == (None, "untrusted skill")


@pytest.mark.parametrize("score", [float("nan"), float("inf"), 10**1000, True, "978"])
def test_malformed_scores_never_prove_positive_credit(score: Any) -> None:
    row, source, analysis, predecessor = _inputs("hung_white")
    analysis.raw_result[0]["score_cp"] = score
    assert credit.positive_move_proof(row, source, analysis, predecessor=predecessor)[0] is None


def test_failed_observation_uses_reached_occurrences_then_canonical_sources() -> None:
    row, source, _, _ = _inputs("hung_white")
    board = chess.Board(source.fen)
    board.halfmove_clock += 4
    board.fullmove_number += 2
    repeated = ingest_fen(board.fen())
    second = row.model_copy(
        update={
            "id": uuid4(),
            "source_position_id": repeated.id,
            "reached_position_id": uuid4(),
            "ply": row.ply + 4,
        }
    )
    blocked = credit._failed_sources(
        [row, second],
        {source.id: source, repeated.id: repeated},
        [(str(second.reached_position_id), credit.CAPTURE_SKILL)],
    )
    assert blocked == {(source.canonical_id, credit.CAPTURE_SKILL)}
    assert (
        credit._failed_sources([row], {source.id: source}, [(str(source.id), credit.CAPTURE_SKILL)])
        == set()
    )


def test_source_score_and_notation_must_match_the_review_occurrence() -> None:
    row, source, analysis, predecessor = _inputs("hung_white")
    row.before_probability = 0.5
    assert credit.positive_move_proof(row, source, analysis, predecessor=predecessor) == (
        None,
        "source score does not match review",
    )
    row, source, analysis, predecessor = _inputs("hung_white")
    row.san = "fake"
    assert credit.positive_move_proof(row, source, analysis, predecessor=predecessor) == (
        None,
        "invalid move notation",
    )
