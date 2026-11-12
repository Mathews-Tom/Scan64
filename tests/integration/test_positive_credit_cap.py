"""Owner-approved per-game/skill budgets on a recorded legal game."""

import json
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.game_review import build_game_review
from scan64.api.models import Player
from scan64.chess.analysis.credit import (
    CAPTURE_SKILL,
    CHECK_SKILL,
    PER_GAME_CREDIT_CAP_REACHED,
    _credit_priority,
    apply_positive_move_credits,
    positive_move_proof,
)
from scan64.chess.analysis.credit_models import PositiveMoveCredit
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.learning.profiling.models import SkillState
from scan64.learning.tolerance import win_probability

RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/m61_credit_cap_game.json").read_text()
)


def _stage(session: Session) -> tuple[Game, PlaySession]:
    game = Game.model_validate(RECORDING["game"])
    play = PlaySession.model_validate(RECORDING["play_session"])
    session.add_all([
        game, play,
        *(Position.model_validate(row) for row in RECORDING["positions"]),
        *(EngineAnalysis.model_validate(row) for row in RECORDING["engine_analyses"]),
        *(GameMoveReview.model_validate(row) for row in RECORDING["review_moves"]),
    ])
    session.commit()
    return game, play


def test_strongest_two_captures_and_earliest_two_mates_are_credited(
    db_session: Session,
) -> None:
    game, play = _stage(db_session)
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    credits = db_session.exec(select(PositiveMoveCredit)).all()
    assert Counter(credit.skill_id for credit in credits) == {CAPTURE_SKILL: 2, CHECK_SKILL: 2}
    # Three captures prove net gains of 1, 6, 10. Later, stronger captures win.
    assert {credit.ply for credit in credits if credit.skill_id == CAPTURE_SKILL} == {5, 13}
    # All three checks prove mate; equal proofs keep the earliest two plies.
    assert {credit.ply for credit in credits if credit.skill_id == CHECK_SKILL} == {11, 15}
    review = build_game_review(game, db_session, include_key_moments=False)
    assert {move.ply for move in review.moves if move.credit} == {5, 11, 13, 15}
    assert {
        move.ply for move in review.moves
        if move.credit_exclusion == PER_GAME_CREDIT_CAP_REACHED
    } == {3, 19}
    skills = db_session.exec(select(SkillState)).all()
    assert len(skills) == 2
    assert all(skill.alpha == skill.prior_alpha + 2 for skill in skills)
    before = [skill.model_dump() for skill in skills]
    # Recovery cannot consume another pair of slots or change alpha.
    play.credit_analysis_status = None
    db_session.add(play)
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    for _ in range(3):
        build_game_review(game, db_session, include_key_moments=False)
    assert len(db_session.exec(select(PositiveMoveCredit)).all()) == 4
    assert [skill.model_dump() for skill in db_session.exec(select(SkillState)).all()] == before


def test_mate_precedes_material_and_ties_keep_earliest_ply() -> None:
    proofs: list[dict[str, Any]] = [
        {"ply": 9, "material_gain": 30},
        {"ply": 7, "material_gain": 0, "score_mate": 4},
        {"ply": 3, "material_gain": 0, "score_mate": 10},
        {"ply": 5, "material_gain": 30},
    ]
    assert [proof["ply"] for proof in sorted(proofs, key=_credit_priority)] == [3, 7, 5, 9]
    # Capture ranking uses the proven net gain including the predecessor.
    assert _credit_priority({"ply": 1, "material_gain": 9, "net_material_gain": 1}) > (
        _credit_priority({"ply": 9, "material_gain": 5, "net_material_gain": 4})
    )


def test_recorded_mating_capture_precedes_higher_gain_non_mating_captures() -> None:
    candidates = []
    for case in RECORDING["ranking_cases"]:
        board = chess.Board(case["fen"])
        before = chess.Board(case["predecessor_fen"])
        game_id = uuid4()
        source = Position(
            game_id=game_id, fen=board.fen(), canonical_id=board.epd(),
            side_to_move="w" if board.turn else "b",
        )
        predecessor = Position(
            game_id=game_id, fen=before.fen(), canonical_id=before.epd(),
            side_to_move="w" if before.turn else "b",
        )
        analysis = EngineAnalysis(position_id=source.id, raw_result=case["raw_result"])
        move = board.parse_uci(case["raw_result"][0]["pv"][0])
        probability = win_probability(case["raw_result"][0])
        assert probability is not None
        row = GameMoveReview(
            game_id=game_id, ply=case["source_ply"], source_position_id=source.id,
            reached_position_id=uuid4(), before_analysis_id=analysis.id,
            after_analysis_id=uuid4(), move_uci=move.uci(), san=board.san(move),
            mover="white" if board.turn else "black", before_probability=probability,
            after_probability=1 - probability,
        )
        proof, reason = positive_move_proof(
            row, source, analysis, predecessor=predecessor,
            preceding_move_uci=case["preceding_move_uci"],
        )
        assert reason is None and proof is not None
        assert proof["skill_id"] == CAPTURE_SKILL and proof["proof_version"] == 1
        assert "score_mate" not in proof  # Existing capture archives remain unchanged.
        candidates.append((proof, analysis))
    selected = sorted(candidates, key=lambda item: _credit_priority(*item))[:2]
    assert [proof["net_material_gain"] for proof, _ in selected] == [1, 13]
    assert selected[0][1].raw_result[0]["score_mate"] == 1


@pytest.mark.parametrize("over_cap", [False, True])
def test_archive_enforces_two_per_game_skill_before_any_restore_writes(
    client: TestClient, db_session: Session, over_cap: bool,
) -> None:
    registered = client.post("/v1/players", json={"id": "cap-owner"})
    assert registered.status_code == 200
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    game, _ = _stage(db_session)
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    exported = client.post("/v1/exports", json={"player_id": "cap-owner"}, headers=headers)
    assert exported.status_code == 200
    archive = exported.json()
    if over_cap:
        row = db_session.exec(select(GameMoveReview).where(
            GameMoveReview.game_id == game.id, GameMoveReview.ply == 3,
        )).one()
        previous = db_session.exec(select(GameMoveReview).where(
            GameMoveReview.game_id == game.id, GameMoveReview.ply == 2,
        )).one()
        source = db_session.get(Position, row.source_position_id)
        analysis = db_session.get(EngineAnalysis, row.before_analysis_id)
        predecessor = db_session.get(Position, previous.source_position_id)
        assert source is not None and analysis is not None
        proof, reason = positive_move_proof(
            row, source, analysis, predecessor=predecessor,
            preceding_move_uci=previous.move_uci,
        )
        assert proof is not None and reason is None
        extra = PositiveMoveCredit(
            player_id="cap-owner", game_id=game.id, canonical_id=source.canonical_id,
            skill_id=CAPTURE_SKILL, ply=3, source_analysis_id=analysis.id,
            reached_analysis_id=row.after_analysis_id, proof=proof,
        )
        archive["positive_move_credits"].append(extra.model_dump(mode="json"))
    deleted = client.request(
        "DELETE", "/v1/players/cap-owner/data", headers=headers,
        json={"dry_run": False, "confirmation": "delete-cap-owner"},
    )
    assert deleted.status_code == 200
    response = client.post("/v1/imports", json=archive, headers=headers)
    if over_cap:
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Invalid archive: malformed assistance or credit provenance"
        )
        assert db_session.get(Player, "cap-owner") is None
        assert db_session.exec(select(PositiveMoveCredit)).all() == []
    else:
        assert response.status_code == 200
        assert Counter(
            credit.skill_id for credit in db_session.exec(select(PositiveMoveCredit)).all()
        ) == {CAPTURE_SKILL: 2, CHECK_SKILL: 2}
