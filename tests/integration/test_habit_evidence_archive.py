from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.reports import read_player_patterns
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, GameMoveReview
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.evidence.models import Evidence
from scan64.learning.habit import record_lesson_credit
from scan64.learning.habit_models import HabitCredit, PatternTarget
from tests.integration.test_habit_archive import PLAYER, _delete, _export, _register
from tests.integration.test_puzzle_mistakes import SKILL, _lesson


def _composed_archive(
    client: TestClient, session: Session, *, with_target: bool = False
) -> tuple[dict[str, Any], dict[str, str]]:
    headers = _register(client)
    opportunity = _lesson(
        session,
        player_id=PLAYER,
        moves=["f2f3", "e7e5", "g2g4", "b8c6"],
        ply=4,
        owner_color="black",
        best_move="d8h4",
    )
    review = session.exec(
        select(GameMoveReview).where(
            GameMoveReview.game_id == opportunity.game_id, GameMoveReview.ply == 4
        )
    ).one()
    source = session.get(Position, review.source_position_id)
    reached = session.get(Position, review.reached_position_id)
    fast = session.get(EngineAnalysis, review.before_analysis_id)
    reply = session.get(EngineAnalysis, review.after_analysis_id)
    assert source is not None and reached is not None and fast is not None and reply is not None
    fast.raw_result = [{"pv": ["d8h4"], "score_mate": 1}]
    reply.raw_result = [{"pv": ["d2d3"], "score_cp": -300}]
    focused = EngineAnalysis(
        position_id=reached.id,
        raw_result=[{"pv": ["d2d3"], "score_cp": -300}],
        config={"nodes": 1_000_000},
    )
    proofs = compose_candidate_evidence(
        before_board=chess.Board(source.fen),
        after_board=chess.Board(reached.fen),
        history_san=["f3", "e5", "g4", "Nc6"],
        initial_fen=None,
        position_id=str(reached.id),
        fast_analysis=fast,
        reply_analysis=reply,
        focused_analysis=focused,
        mate_source_analysis=None,
        played_move="Nc6",
        swing_cp=900,
    )
    proof = next(row for row in proofs if row.payload.get("missed_type") == "check")
    spec = deepcopy(opportunity.lesson_spec)
    old_proof = session.get(Evidence, spec["diagnosis"]["evidence_refs"][0])
    assert old_proof is not None
    session.delete(old_proof)
    spec["diagnosis"]["evidence_refs"] = [proof.evidence_id]
    opportunity.lesson_spec = spec
    session.add_all([fast, reply, focused, opportunity, *proofs])
    study = StudySession(player_id=PLAYER, domain="training")
    attempt = LessonAttempt(
        session_id=study.id,
        player_id=PLAYER,
        lesson_id=str(opportunity.id),
        opportunity_id=opportunity.id,
        source_kind="persisted_opportunity",
        submitted_move="d8h4",
        hints_used=0,
        elapsed_ms=500,
        success=True,
        grading_status="verified",
        profile_update_result="applied",
    )
    session.add_all([study, attempt])
    session.flush()
    record_lesson_credit(attempt, session)
    session.commit()
    assert session.exec(select(HabitCredit).where(HabitCredit.player_id == PLAYER)).one()
    if with_target:
        opportunities = [opportunity]
        for _ in range(2):
            opportunities.append(
                _lesson(
                    session,
                    player_id=PLAYER,
                    moves=["f2f3", "e7e5", "g2g4", "b8c6"],
                    ply=4,
                    owner_color="black",
                    best_move="d8h4",
                )
            )
        completed = datetime.now(UTC)
        session.add_all(
            AnalysisJob(
                game_id=row.game_id,
                status="completed",
                created_at=completed,
                completed_at=completed,
            )
            for row in opportunities
        )
        session.flush()
        snapshot = (
            read_player_patterns(PLAYER, session).recurring_diagnoses[0].model_dump(mode="json")
        )
        session.add(PatternTarget(player_id=PLAYER, code=SKILL, pattern_snapshot=snapshot))
        session.commit()
    archive = _export(client, headers)
    _delete(client, headers)
    session.expire_all()
    return archive, headers


@pytest.mark.parametrize("include_review_moves", [True, False])
def test_composed_post_move_habit_evidence_roundtrips(
    client: TestClient, db_session: Session, include_review_moves: bool
) -> None:
    archive, headers = _composed_archive(client, db_session)
    if not include_review_moves:
        archive.pop("review_moves")
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = _export(client, headers)
    assert restored["habit_credits"] == archive["habit_credits"]
    assert restored["habit_days"] == archive["habit_days"]
    assert restored["evidence"] == archive["evidence"]
    assert restored["lesson_attempts"] == archive["lesson_attempts"]


@pytest.mark.parametrize("ledger", ["credit", "pattern"])
@pytest.mark.parametrize(
    "tamper",
    [
        "before_fen",
        "after_fen",
        "played_move",
        "fast_analysis_id",
        "reply_analysis_id",
        "focused_analysis_id",
        "missing_provenance",
        "malformed_analysis_id",
        "unplayed_legal_move",
        "other_owned_game",
    ],
)
def test_post_move_habit_evidence_rejects_unbound_provenance(
    client: TestClient, db_session: Session, tamper: str, ledger: str
) -> None:
    archive, headers = _composed_archive(client, db_session, with_target=ledger == "pattern")
    if ledger == "pattern":
        archive["habit_credits"] = []
        archive["habit_days"] = []
    proof = next(row for row in archive["evidence"] if row["payload"].get("missed_type") == "check")
    payload = proof["payload"]
    if tamper in ("before_fen", "after_fen"):
        payload[tamper] = chess.STARTING_FEN
    elif tamper == "played_move":
        payload["played_move"] = "Qh4#"
    elif tamper in ("fast_analysis_id", "reply_analysis_id", "focused_analysis_id"):
        payload[tamper] = (
            payload["reply_analysis_id"]
            if tamper == "fast_analysis_id"
            else payload["fast_analysis_id"]
        )
    elif tamper == "missing_provenance":
        proof["payload"] = {}
    elif tamper == "malformed_analysis_id":
        payload["fast_analysis_id"] = []
    else:
        reached = deepcopy(
            next(row for row in archive["positions"] if row["id"] == proof["position_id"])
        )
        reached["id"] = str(uuid4())
        if tamper == "unplayed_legal_move":
            board = chess.Board(payload["before_fen"])
            board.push_san("d6")
            reached["fen"] = board.fen()
            payload["played_move"] = "d6"
        else:
            game = deepcopy(
                next(row for row in archive["games"] if row["id"] == reached["game_id"])
            )
            game["id"] = str(uuid4())
            archive["games"].append(game)
            reached["game_id"] = game["id"]
        archive["positions"].append(reached)
        for field in ("focused_analysis_id", "reply_analysis_id"):
            analysis = deepcopy(
                next(row for row in archive["engine_analyses"] if row["id"] == payload[field])
            )
            analysis["id"] = str(uuid4())
            analysis["position_id"] = reached["id"]
            archive["engine_analyses"].append(analysis)
            payload[field] = analysis["id"]
        proof["position_id"] = reached["id"]
        proof["engine_analysis_id"] = payload["focused_analysis_id"]
        payload["after_fen"] = reached["fen"]
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert db_session.exec(select(HabitCredit).where(HabitCredit.player_id == PLAYER)).all() == []


@pytest.mark.parametrize("keep_credit", [True, False])
def test_pattern_target_with_composed_post_move_evidence_roundtrips(
    client: TestClient, db_session: Session, keep_credit: bool
) -> None:
    archive, headers = _composed_archive(client, db_session, with_target=True)
    if not keep_credit:
        archive["habit_credits"] = []
        archive["habit_days"] = []
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = _export(client, headers)
    assert restored["pattern_targets"] == archive["pattern_targets"]
    assert restored["habit_credits"] == archive["habit_credits"]
    assert restored["evidence"] == archive["evidence"]


def test_habit_archive_rejects_illegal_owned_mainline_without_server_error(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _composed_archive(client, db_session)
    archive.pop("review_moves")
    archive["games"][0]["moves"][3] = "a6a5"
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert db_session.exec(select(HabitCredit).where(HabitCredit.player_id == PLAYER)).all() == []
