from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.data_lifecycle import ExportArchive, _validate_habit_archive
from scan64.api.reports import read_player_patterns
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, GameMoveReview
from scan64.chess.analysis.review import stage_game_review
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.habit_models import (
    HABIT_POLICY_VERSION,
    HabitCredit,
    HabitDay,
    HabitGoal,
    HabitPolicy,
    PatternAward,
    PatternAwardEvidence,
    PatternTarget,
)
from scan64.learning.profiling.models import ProfileObservation
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from tests.integration.test_puzzle_lifecycle import seed_puzzle_run
from tests.integration.test_puzzle_mistakes import SKILL, _lesson

PLAYER = "habit-archive-owner"
HABIT_FIELDS = (
    "habit_policy",
    "habit_goals",
    "habit_days",
    "habit_credits",
    "pattern_targets",
    "pattern_awards",
    "pattern_award_evidence",
)


def _register(client: TestClient) -> dict[str, str]:
    response = client.post("/v1/players", json={"id": PLAYER})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _delete(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.request(
        "DELETE",
        f"/v1/players/{PLAYER}/data",
        headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{PLAYER}"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["affected_rows"])


def _export(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post("/v1/exports", json={"player_id": PLAYER}, headers=headers)
    assert response.status_code == 200
    return dict(response.json())


def _seed_archive(
    client: TestClient,
    session: Session,
    *,
    award: bool = True,
) -> tuple[dict[str, Any], dict[str, str]]:
    headers = _register(client)
    start = (datetime.now(UTC) - timedelta(days=14)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    policy = session.get(HabitPolicy, PLAYER)
    assert policy is not None
    policy.effective_at = start - timedelta(days=2)
    session.add(policy)
    lesson_inputs = (
        (["d2d4"], 1, "e2e4"),
        (["e2e4", "e7e5", "g1f3"], 3, "b1c3"),
        (["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"], 5, "f1c4"),
    )
    opportunities = []
    for index, (moves, ply, best) in enumerate(lesson_inputs):
        opportunity = _lesson(
            session,
            player_id=PLAYER,
            moves=moves,
            ply=ply,
            best_move=best,
            created_at=start - timedelta(hours=3 - index),
        )
        game = session.get(Game, opportunity.game_id)
        assert game is not None
        game.created_at = start - timedelta(days=1)
        session.add(game)
        session.add(
            AnalysisJob(
                game_id=game.id,
                status="completed",
                created_at=start - timedelta(hours=5),
                completed_at=start - timedelta(hours=4),
            )
        )
        opportunities.append(opportunity)
    session.commit()
    pattern = read_player_patterns(PLAYER, session).recurring_diagnoses[0].model_dump(mode="json")
    target = PatternTarget(player_id=PLAYER, code=SKILL, started_at=start, pattern_snapshot=pattern)
    completed_times = []
    for index, opportunity in enumerate(opportunities):
        completed = start + timedelta(hours=index + 1)
        completed_times.append(completed)
        study = StudySession(player_id=PLAYER, domain="training", started_at=start)
        session.add(study)
        attempt = LessonAttempt(
            session_id=study.id,
            player_id=PLAYER,
            lesson_id=str(opportunity.id),
            source_kind="persisted_opportunity",
            opportunity_id=opportunity.id,
            submitted_move=lesson_inputs[index][2],
            elapsed_ms=500,
            hints_used=0,
            success=True,
            grading_status="verified",
            profile_update_result="applied",
            completed_at=completed,
        )
        session.add(attempt)
        source = session.get(Position, opportunity.source_position_id)
        assert source is not None
        epd = chess.Board(source.fen).epd()
        refs = opportunity.lesson_spec["diagnosis"]["evidence_refs"]
        session.add(
            HabitCredit(
                player_id=PLAYER,
                utc_day=start.date().isoformat(),
                source_kind="lesson",
                source_id=attempt.id,
                source_key=f"lesson:{opportunity.id}",
                source_epd=epd,
                completed_at=completed,
                trust_snapshot={
                    "opportunity_id": str(opportunity.id),
                    "game_id": str(opportunity.game_id),
                    "position_id": str(source.id),
                    "code": SKILL,
                    "verification_analysis_id": str(opportunity.verification_analysis_id),
                    "evidence_references": refs,
                    "hints_authority": "app_reported",
                    "hints_used": 0,
                },
            )
        )
        target.practice.append(
            {
                "attempt_id": attempt.id,
                "opportunity_id": str(opportunity.id),
                "game_id": str(opportunity.game_id),
                "source_epd": epd,
                "completed_at": completed.isoformat(),
            }
        )
    target.practice_completed_at = completed_times[-1]
    session.add(
        HabitDay(
            player_id=PLAYER,
            utc_day=start.date().isoformat(),
            goal=3,
            met_at=completed_times[-1],
        )
    )
    session.add(
        HabitGoal(
            player_id=PLAYER,
            effective_day=(start + timedelta(days=1)).date().isoformat(),
            goal=4,
            created_at=start + timedelta(hours=4),
        )
    )
    window_jobs = []
    if award:
        for index in range(5):
            created = start + timedelta(days=1, hours=index)
            game = Game(
                pgn="",
                owner_player_id=PLAYER,
                owner_color="white",
                result="1/2-1/2",
                moves=["g1f3", "g8f6", "f3g1", "f6g8"] * 10,
                created_at=created,
            )
            session.add(game)
            session.add(PlaySession(player_id=PLAYER, game_id=game.id, status="completed"))
            job = AnalysisJob(
                game_id=game.id,
                status="completed",
                created_at=created,
                completed_at=created + timedelta(minutes=10),
            )
            session.add(job)
            window_jobs.append(job)
            positions: dict[str, Position] = {}
            analyses: dict[UUID, EngineAnalysis] = {}
            rows = stage_game_review(
                game,
                [EngineAnalysis(raw_result=[{"score_cp": 0}]) for _ in range(41)],
                positions,
                analyses,
                set(),
            )
            session.add_all(positions.values())
            session.add_all(analyses.values())
            session.add_all(rows)
            target.window_game_ids.append(str(game.id))
        session.add(target)
        session.flush()
        earned_award = PatternAward(
            player_id=PLAYER,
            target_id=target.id,
            code=SKILL,
            window_start=target.window_game_ids[0],
            game_ids=list(target.window_game_ids),
            awarded_at=start + timedelta(days=1, hours=4, minutes=10),
        )
        session.add(earned_award)
        session.flush()
        for ordinal, job in enumerate(window_jobs, 1):
            session.add(PatternAwardEvidence(
                player_id=PLAYER,
                award_id=earned_award.id,
                ordinal=ordinal,
                game_id=job.game_id,
                analysis_job_id=job.id,
                game_created_at=job.created_at,
                job_completed_at=job.completed_at,
            ))
    else:
        session.add(target)
    session.commit()
    archive = _export(client, headers)
    _delete(client, headers)
    session.expire_all()
    return archive, headers


def test_habit_archive_restores_cutover_ledgers_and_award_without_replay(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = _export(client, headers)
    for field in HABIT_FIELDS:
        assert restored[field] == archive[field]
    assert len(restored["habit_credits"]) == 3
    assert len(restored["pattern_awards"]) == 1
    assert len(restored["pattern_award_evidence"]) == 5
    assert restored["skill_states"] == archive["skill_states"]
    assert restored["puzzle_rating_events"] == archive["puzzle_rating_events"]
    dry_run = client.request(
        "DELETE",
        f"/v1/players/{PLAYER}/data",
        headers=headers,
        json={"dry_run": True},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["affected_rows"]["habit_credits"] == 3
    assert dry_run.json()["affected_rows"]["pattern_awards"] == 1
    assert dry_run.json()["affected_rows"]["pattern_award_evidence"] == 5
    assert _export(client, headers) == restored
    from scan64.api.models import DeletionAudit

    audit_ids = {row.id for row in db_session.exec(select(DeletionAudit)).all()}

    def fail_commit() -> None:
        raise RuntimeError("injected habit deletion failure")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected habit deletion failure"):
            _delete(client, headers)
    db_session.rollback()
    db_session.expire_all()
    assert _export(client, headers) == restored
    assert {row.id for row in db_session.exec(select(DeletionAudit)).all()} == audit_ids
    counts = _delete(client, headers)
    assert {field: counts[field] for field in HABIT_FIELDS} == {
        "habit_policy": 1,
        "habit_goals": 1,
        "habit_days": 1,
        "habit_credits": 3,
        "pattern_targets": 1,
        "pattern_awards": 1,
        "pattern_award_evidence": 5,
    }
    audits = db_session.exec(select(DeletionAudit)).all()
    newest = next(row for row in audits if row.id not in audit_ids)
    assert all(newest.affected_rows[field] == counts[field] for field in HABIT_FIELDS)
    db_session.expire_all()
    for model in (
        HabitPolicy, HabitGoal, HabitDay, HabitCredit, PatternTarget, PatternAward,
        PatternAwardEvidence,
    ):
        assert db_session.exec(select(model).where(model.player_id == PLAYER)).all() == []


@pytest.mark.parametrize("field", HABIT_FIELDS)
def test_habit_archive_rejects_foreign_owners(
    client: TestClient,
    db_session: Session,
    field: str,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    row = archive[field] if field == "habit_policy" else archive[field][0]
    row["player_id"] = "foreign-owner"
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "tamper",
    [
        "unknown_policy",
        "missing_policy",
        "pre_cutover",
        "boolean_goal",
        "same_day_goal",
        "forged_met_at",
        "forged_goal",
        "duplicate_epd",
        "duplicate_source",
        "wrong_day",
        "wrong_grade",
        "hinted_lesson",
        "earlier_attempt",
        "forged_trust",
        "forged_epd",
        "forged_evidence",
        "forged_game_source",
        "practice_before_start",
        "practice_repeat",
        "practice_one_game",
        "practice_false_completion",
        "window_import",
        "window_short",
        "window_unscored",
        "window_unfinished",
        "window_order",
        "window_recurrence",
        "window_retired_after_award",
        "award_duplicate",
        "award_wrong_policy",
        "award_wrong_start",
        "award_wrong_completion",
        "pattern_forged_reference",
        "pattern_forged_occurrence",
        "invalidation_without_reason",
        "extra_snapshot_field",
    ],
)
def test_habit_archive_rejects_tampered_provenance_atomically(
    client: TestClient,
    db_session: Session,
    tamper: str,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    target = archive["pattern_targets"][0]
    credit = archive["habit_credits"][0]
    attempt = next(row for row in archive["lesson_attempts"] if row["id"] == credit["source_id"])
    award = archive["pattern_awards"][0]
    if tamper == "unknown_policy":
        archive["habit_policy"]["policy_version"] = "habit-forged"
    elif tamper == "missing_policy":
        archive["habit_policy"] = None
    elif tamper == "pre_cutover":
        archive["habit_policy"]["effective_at"] = award["awarded_at"]
    elif tamper == "boolean_goal":
        archive["habit_goals"][0]["goal"] = True
    elif tamper == "same_day_goal":
        archive["habit_goals"][0]["effective_day"] = archive["habit_days"][0]["utc_day"]
    elif tamper == "forged_met_at":
        archive["habit_days"][0]["met_at"] = target["started_at"]
    elif tamper == "forged_goal":
        archive["habit_days"][0]["goal"] = 1
    elif tamper == "duplicate_epd":
        archive["habit_credits"][1]["source_epd"] = credit["source_epd"]
    elif tamper == "duplicate_source":
        duplicate = deepcopy(credit)
        duplicate["id"] = str(uuid4())
        archive["habit_credits"].append(duplicate)
    elif tamper == "wrong_day":
        credit["utc_day"] = "2001-01-01"
    elif tamper == "wrong_grade":
        attempt["submitted_move"] = "a2a3"
    elif tamper == "hinted_lesson":
        attempt["hints_used"] = 1
    elif tamper == "earlier_attempt":
        earlier = deepcopy(attempt)
        earlier.update(id=str(uuid4()), completed_at=target["started_at"], success=False)
        archive["lesson_attempts"].append(earlier)
    elif tamper == "forged_trust":
        credit["trust_snapshot"]["verification_analysis_id"] = str(uuid4())
    elif tamper == "forged_epd":
        credit["source_epd"] = "forged"
    elif tamper == "forged_evidence":
        credit["trust_snapshot"]["evidence_references"] = ["missing-proof"]
    elif tamper == "forged_game_source":
        next(row for row in archive["games"] if row["id"] == credit["trust_snapshot"]["game_id"])[
            "moves"
        ] = []
    elif tamper == "practice_before_start":
        target["started_at"] = target["practice_completed_at"]
    elif tamper == "practice_repeat":
        target["practice"][1] = deepcopy(target["practice"][0])
    elif tamper == "practice_one_game":
        target["practice"][1]["game_id"] = target["practice"][0]["game_id"]
    elif tamper == "practice_false_completion":
        target["practice_completed_at"] = target["started_at"]
    elif tamper == "window_import":
        archive["play_sessions"] = [
            row for row in archive["play_sessions"] if row["game_id"] != award["game_ids"][0]
        ]
    elif tamper == "window_short":
        next(row for row in archive["games"] if row["id"] == award["game_ids"][0])["moves"] = [
            "g1f3",
            "g8f6",
        ]
    elif tamper == "window_unscored":
        next(
            row
            for row in archive["review_moves"]
            if row["game_id"] == award["game_ids"][0] and row["mover"] == "white"
        )["before_probability"] = None
    elif tamper == "window_unfinished":
        next(row for row in archive["analysis_jobs"] if row["game_id"] == award["game_ids"][0])[
            "status"
        ] = "pending"
    elif tamper == "window_order":
        target["window_game_ids"].reverse()
        award["game_ids"].reverse()
        award["window_start"] = award["game_ids"][0]
    elif tamper in {"window_recurrence", "window_retired_after_award"}:
        game_id = award["game_ids"][1]
        source = next(row for row in archive["positions"] if row["game_id"] == game_id)
        observation = ProfileObservation(
            player_id=PLAYER,
            game_id=game_id,
            position_id=source["id"],
            skill_id=SKILL,
            observed_at=datetime.fromisoformat(target["practice_completed_at"]),
            retired_at=(
                datetime.fromisoformat(award["awarded_at"]) + timedelta(days=1)
                if tamper == "window_retired_after_award"
                else None
            ),
        )
        archive["profile_observations"].append(observation.model_dump(mode="json"))
    elif tamper == "award_duplicate":
        duplicate = deepcopy(award)
        duplicate["id"] = str(uuid4())
        archive["pattern_awards"].append(duplicate)
    elif tamper == "award_wrong_policy":
        award["policy_version"] = "habit-other"
    elif tamper == "award_wrong_start":
        award["window_start"] = award["game_ids"][1]
    elif tamper == "award_wrong_completion":
        award["awarded_at"] = target["practice_completed_at"]
    elif tamper == "pattern_forged_reference":
        target["pattern_snapshot"]["evidence_references"] = ["forged"]
    elif tamper == "pattern_forged_occurrence":
        target["pattern_snapshot"]["supporting_moves"][0]["ply"] = 2
    elif tamper == "invalidation_without_reason":
        target["invalidated_at"] = award["awarded_at"]
    elif tamper == "extra_snapshot_field":
        credit["trust_snapshot"]["server_proven_independence"] = True
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert db_session.get(HabitPolicy, PLAYER) is None
    assert db_session.exec(select(HabitCredit).where(HabitCredit.player_id == PLAYER)).all() == []


def test_habit_award_archive_rejects_conflicting_first_duplicate_game_before_writes(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    assert archive["pattern_awards"] and not archive["positive_move_credits"]
    game_id = archive["pattern_awards"][0]["game_ids"][0]
    duplicate = deepcopy(next(row for row in archive["games"] if row["id"] == game_id))
    duplicate["created_at"] = archive["pattern_targets"][0]["practice_completed_at"]
    archive["games"].insert(0, duplicate)
    before = db_session.connection().connection.total_changes
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Invalid archive: duplicate source IDs"
    assert db_session.connection().connection.total_changes == before
    assert db_session.get(HabitPolicy, PLAYER) is None
    assert db_session.get(Game, UUID(game_id)) is None


@pytest.mark.parametrize(
    "collection",
    [
        "games", "positions", "engine_analyses", "analysis_jobs",
        "lesson_opportunities", "lesson_attempts", "evidence",
    ],
)
def test_habit_award_archive_rejects_duplicate_source_ids_without_positive_credits(
    client: TestClient,
    db_session: Session,
    collection: str,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    assert archive["pattern_awards"] and not archive["positive_move_credits"]
    archive[collection].insert(0, deepcopy(archive[collection][0]))
    before = db_session.connection().connection.total_changes
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Invalid archive: duplicate source IDs"
    assert db_session.connection().connection.total_changes == before
    assert db_session.get(HabitPolicy, PLAYER) is None


def test_habit_archive_preserves_withdrawn_historical_day_and_award(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    target = archive["pattern_targets"][0]
    withdrawn_at = datetime.fromisoformat(archive["pattern_awards"][0]["awarded_at"]) + timedelta(
        days=1
    )
    target["invalidated_at"] = withdrawn_at.isoformat()
    target["invalidation_reason"] = (
        "A supporting lesson is withdrawn, retired, untrusted, or no longer owned."
    )
    for opportunity in archive["lesson_opportunities"]:
        opportunity["verification_status"] = "invalid"
        opportunity["verification_error"] = "withdrawn after earning"
    for schedule in archive["review_schedules"]:
        schedule["retired_at"] = withdrawn_at.isoformat()
        schedule["retirement_reason"] = "withdrawn after earning"
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = _export(client, headers)
    assert restored["habit_days"] == archive["habit_days"]
    assert restored["habit_credits"] == archive["habit_credits"]
    assert restored["pattern_awards"] == archive["pattern_awards"]
    assert restored["pattern_targets"][0]["invalidation_reason"] == target["invalidation_reason"]


def test_legacy_archive_missing_habit_fields_gets_fresh_cutover_without_backcredit(
    client: TestClient,
    db_session: Session,
) -> None:
    headers = _register(client)
    run = seed_puzzle_run(db_session, PLAYER, solved=True)
    archive = _export(client, headers)
    old_completed = run.completed_at
    assert old_completed is not None
    _delete(client, headers)
    for field in HABIT_FIELDS:
        archive.pop(field)
    before = datetime.now(UTC)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = _export(client, headers)
    cutover = datetime.fromisoformat(restored["habit_policy"]["effective_at"])
    if cutover.tzinfo is None:
        cutover = cutover.replace(tzinfo=UTC)
    assert cutover >= before
    assert restored["habit_policy"]["policy_version"] == HABIT_POLICY_VERSION
    assert restored["habit_credits"] == []
    assert restored["habit_days"] == []
    assert restored["puzzle_runs"][0]["state"] == "solved"


def test_habit_archive_puzzle_requires_matching_unhinted_terminal_attempt(
    client: TestClient,
    db_session: Session,
) -> None:
    headers = _register(client)
    run = seed_puzzle_run(db_session, PLAYER, solved=True)
    assert run.completed_at is not None
    completed = run.completed_at.replace(tzinfo=UTC)
    policy = db_session.get(HabitPolicy, PLAYER)
    assert policy is not None
    policy.effective_at = completed - timedelta(days=1)
    db_session.add(policy)
    db_session.add(HabitDay(player_id=PLAYER, utc_day=completed.date().isoformat(), goal=3))
    board = chess.Board()
    board.push_uci("e2e4")
    db_session.add(
        HabitCredit(
            player_id=PLAYER,
            utc_day=completed.date().isoformat(),
            source_kind="puzzle",
            source_id=run.id,
            source_key=f"puzzle:{run.item_id}",
            source_epd=board.epd(),
            completed_at=completed,
            trust_snapshot={
                "item_id": run.item_id,
                "run_id": run.id,
                "content_attempt_id": run.id,
                "hints_authority": "server",
                "hint_steps": {},
            },
        )
    )
    db_session.commit()
    archive = _export(client, headers)
    _validate_habit_archive(db_session, ExportArchive.model_validate(archive), PLAYER)
    for field, value in (("hint_assisted", True), ("success", False), ("completed_at", None)):
        tampered = deepcopy(archive)
        tampered["content_attempts"][0][field] = value
        with pytest.raises(ValueError):
            _validate_habit_archive(db_session, ExportArchive.model_validate(tampered), PLAYER)
    tampered = deepcopy(archive)
    tampered["puzzle_runs"][0]["hint_steps"] = {"0": 1}
    with pytest.raises(ValueError):
        _validate_habit_archive(db_session, ExportArchive.model_validate(tampered), PLAYER)


def test_habit_archive_accepts_out_of_order_window_analysis_completion(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    award = archive["pattern_awards"][0]
    last_completion = datetime.fromisoformat(award["awarded_at"]) + timedelta(hours=1)
    # The chronologically first game finishes analysis last; game order remains
    # server created_at/id, not analysis-completion order.
    first_job = next(
        row for row in archive["analysis_jobs"] if row["game_id"] == award["game_ids"][0]
    )
    first_job["completed_at"] = last_completion.isoformat()
    archive["pattern_award_evidence"][0]["job_completed_at"] = last_completion.isoformat()
    award["awarded_at"] = last_completion.isoformat()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = _export(client, headers)
    assert restored["pattern_awards"] == archive["pattern_awards"]


def test_habit_archive_accepts_legacy_position_labels_and_custom_lesson_sources(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    for position in archive["positions"]:
        position["canonical_id"] = "legacy-position-label"
    for opportunity in archive["lesson_opportunities"]:
        opportunity["lesson_spec"]["source"]["kind"] = "custom"
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    assert _export(client, headers)["habit_credits"] == archive["habit_credits"]


@pytest.mark.parametrize("tamper", ["orphaned_award_evidence", "unknown_reason", "boolean_hints"])
def test_habit_archive_rejects_uncommitted_or_untyped_history(
    client: TestClient,
    db_session: Session,
    tamper: str,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    if tamper == "orphaned_award_evidence":
        # Evidence needs its immutable award; a partial target alone does not
        # imply a missing award via global historical reconstruction.
        archive["pattern_awards"] = []
    elif tamper == "unknown_reason":
        target = archive["pattern_targets"][0]
        target["invalidated_at"] = archive["pattern_awards"][0]["awarded_at"]
        target["invalidation_reason"] = "A forged administrative reason"
    else:
        archive["lesson_attempts"][0]["hints_used"] = False
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text


def test_habit_archive_ignores_unrecorded_analysis_before_persisted_span(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    award = archive["pattern_awards"][0]
    original_game_id = award["game_ids"][0]
    inserted_game_id = str(uuid4())
    original_game = next(row for row in archive["games"] if row["id"] == original_game_id)
    inserted = deepcopy(original_game)
    inserted["id"] = inserted_game_id
    inserted["created_at"] = (
        datetime.fromisoformat(original_game["created_at"]) - timedelta(minutes=5)
    ).isoformat()
    archive["games"].append(inserted)
    archive["play_sessions"].append(
        PlaySession(
            player_id=PLAYER,
            game_id=UUID(inserted_game_id),
            status="completed",
        ).model_dump(mode="json")
    )
    archive["analysis_jobs"].append(
        AnalysisJob(
            game_id=UUID(inserted_game_id),
            status="completed",
            created_at=datetime.fromisoformat(inserted["created_at"]),
            completed_at=datetime.fromisoformat(award["awarded_at"]) + timedelta(days=1),
        ).model_dump(mode="json")
    )
    # Restore validates the recorded award span, not global historical reconstruction.
    position_ids: dict[str, str] = {}
    for position in list(archive["positions"]):
        if position["game_id"] == original_game_id:
            replacement = deepcopy(position)
            replacement["id"] = str(uuid4())
            replacement["game_id"] = inserted_game_id
            position_ids[position["id"]] = replacement["id"]
            archive["positions"].append(replacement)
    analysis_ids: dict[str, str] = {}
    for analysis in list(archive["engine_analyses"]):
        if analysis["position_id"] in position_ids:
            replacement = deepcopy(analysis)
            replacement["id"] = str(uuid4())
            replacement["position_id"] = position_ids[analysis["position_id"]]
            analysis_ids[analysis["id"]] = replacement["id"]
            archive["engine_analyses"].append(replacement)
    for review in list(archive["review_moves"]):
        if review["game_id"] == original_game_id:
            replacement = deepcopy(review)
            replacement["id"] = str(uuid4())
            replacement["game_id"] = inserted_game_id
            replacement["source_position_id"] = position_ids[review["source_position_id"]]
            replacement["reached_position_id"] = position_ids[review["reached_position_id"]]
            replacement["before_analysis_id"] = analysis_ids[review["before_analysis_id"]]
            replacement["after_analysis_id"] = analysis_ids[review["after_analysis_id"]]
            archive["review_moves"].append(replacement)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("preceding_games", [2, 4])
def test_analysis_time_invalidation_freezes_a_restorable_window(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    preceding_games: int,
) -> None:
    import tests.unit.test_habit as fixtures

    archive, headers = _seed_archive(client, db_session, award=False)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    monkeypatch.setattr(fixtures, "PLAYER", PLAYER)
    target = db_session.exec(select(PatternTarget)).one()
    assert target.practice_completed_at is not None
    for index in range(preceding_games):
        fixtures._complete(
            fixtures._scored_game(
                db_session,
                target.practice_completed_at + timedelta(hours=index + 1),
            ),
            db_session,
        )
    supporting = target.pattern_snapshot["supporting_moves"][0]["lesson_id"]
    schedule = db_session.get(ReviewSchedule, (PLAYER, supporting))
    assert schedule is not None
    schedule.retired_at = datetime.now(UTC)
    schedule.retirement_reason = "reviewed withdrawal"
    db_session.add(schedule)
    fixtures._complete(
        fixtures._scored_game(
            db_session,
            target.practice_completed_at + timedelta(hours=preceding_games + 1),
        ),
        db_session,
    )
    db_session.commit()
    assert target.invalidated_at is not None
    assert len(target.window_game_ids) == preceding_games + 1
    assert db_session.exec(select(PatternAward)).all() == []
    exported = _export(client, headers)
    _delete(client, headers)
    restored = client.post("/v1/imports", json=exported, headers=headers)
    assert restored.status_code == 200, restored.text
    assert _export(client, headers)["pattern_targets"] == exported["pattern_targets"]


def test_archive_ignores_unrecorded_pending_game_before_persisted_span(
    client: TestClient,
    db_session: Session,
) -> None:
    from uuid import uuid4

    archive, headers = _seed_archive(client, db_session)
    first_id = archive["pattern_awards"][0]["game_ids"][0]
    first = next(row for row in archive["games"] if row["id"] == first_id)
    earlier = deepcopy(first)
    earlier["id"] = str(uuid4())
    earlier["created_at"] = (
        datetime.fromisoformat(first["created_at"]) - timedelta(hours=1)
    ).isoformat()
    archive["games"].append(earlier)
    play = deepcopy(next(row for row in archive["play_sessions"] if row["game_id"] == first_id))
    play.update(id=str(uuid4()), game_id=earlier["id"])
    archive["play_sessions"].append(play)
    job = deepcopy(next(row for row in archive["analysis_jobs"] if row["game_id"] == first_id))
    job.update(id=str(uuid4()), game_id=earlier["id"], status="pending", completed_at=None)
    archive["analysis_jobs"].append(job)
    response = client.post("/v1/imports", json=archive, headers=headers)
    # Unrecorded games outside the immutable span cannot rewrite this award.
    assert response.status_code == 200, response.text


def test_interleaved_active_session_and_resolving_award_both_roundtrip(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tests.unit.test_habit as fixtures

    archive, headers = _seed_archive(client, db_session, award=False)
    assert client.post("/v1/imports", json=archive, headers=headers).status_code == 200
    monkeypatch.setattr(fixtures, "PLAYER", PLAYER)
    target = db_session.exec(select(PatternTarget)).one()
    assert target.practice_completed_at is not None
    jobs = [
        fixtures._scored_game(
            db_session,
            target.practice_completed_at + timedelta(hours=index + 1),
        )
        for index in range(6)
    ]
    first_job_id = jobs[0].id
    game = db_session.get(Game, jobs[0].game_id)
    play = db_session.exec(select(PlaySession).where(PlaySession.game_id == game.id)).one()
    game.result, play.status = "*", "active"
    db_session.add_all([game, play])
    db_session.flush()
    for job in jobs[1:]:
        fixtures._complete(job, db_session)
    db_session.commit()
    assert db_session.exec(select(PatternAward)).all() == []
    waiting = _export(client, headers)
    _delete(client, headers)
    restored = client.post("/v1/imports", json=waiting, headers=headers)
    assert restored.status_code == 200, restored.text
    job = db_session.get(AnalysisJob, first_job_id)
    game = db_session.get(Game, job.game_id)
    play = db_session.exec(select(PlaySession).where(PlaySession.game_id == game.id)).one()
    game.result, play.status = "1/2-1/2", "completed"
    db_session.add_all([game, play])
    fixtures._complete(job, db_session)
    db_session.commit()
    assert len(db_session.exec(select(PatternAward)).all()) == 1
    awarded = _export(client, headers)
    _delete(client, headers)
    restored = client.post("/v1/imports", json=awarded, headers=headers)
    assert restored.status_code == 200, restored.text


@pytest.mark.parametrize("invalidated", [False, True])
def test_active_barrier_finished_short_after_freeze_remains_restorable(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    invalidated: bool,
) -> None:
    import tests.unit.test_habit as fixtures

    archive, headers = _seed_archive(client, db_session, award=False)
    assert client.post("/v1/imports", json=archive, headers=headers).status_code == 200
    monkeypatch.setattr(fixtures, "PLAYER", PLAYER)
    target = db_session.exec(select(PatternTarget)).one()
    assert target.practice_completed_at is not None
    earlier = fixtures._scored_game(
        db_session,
        target.practice_completed_at + timedelta(hours=1),
    )
    game = db_session.get(Game, earlier.game_id)
    play = db_session.exec(select(PlaySession).where(PlaySession.game_id == game.id)).one()
    game.result, play.status = "*", "active"
    db_session.add_all([game, play])
    later = fixtures._scored_game(
        db_session,
        target.practice_completed_at + timedelta(hours=2),
    )
    fixtures._complete(later, db_session)
    db_session.commit()
    assert target.window_game_ids == []
    if invalidated:
        supporting = target.pattern_snapshot["supporting_moves"][0]["lesson_id"]
        schedule = db_session.get(ReviewSchedule, (PLAYER, supporting))
        schedule.retired_at = datetime.now(UTC)
        schedule.retirement_reason = "reviewed withdrawal"
        db_session.add(schedule)
        db_session.commit()
        response = client.post(
            f"/v1/players/{PLAYER}/habit/targets/{target.id}/refresh",
            json={},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        db_session.refresh(target)
        assert target.invalidated_at is not None
    game.moves, game.result, play.status = game.moves[:2], "1/2-1/2", "completed"
    for review in db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == game.id)
    ).all():
        db_session.delete(review)
    db_session.add_all([game, play])
    if invalidated:
        fixtures._complete(earlier, db_session)
    else:
        earlier.status, earlier.error = "failed", "fixture engine failure"
        db_session.add(earlier)
    db_session.commit()
    assert target.window_game_ids == []
    if not invalidated:
        from scan64.learning.habit import habit_summary

        blocker = habit_summary(PLAYER, db_session)["targets"][0]["blocking_game"]
        assert blocker["game_id"] == str(game.id) and blocker["reason"] == "analysis"
    exported = _export(client, headers)
    _delete(client, headers)
    restored = client.post("/v1/imports", json=exported, headers=headers)
    assert restored.status_code == 200, restored.text
    assert _export(client, headers)["pattern_targets"] == exported["pattern_targets"]


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_row", "missing_collection", "swapped_games", "swapped_jobs",
        "unfinished_job", "missing_job", "out_of_order", "duplicate_ordinal",
        "boolean_ordinal", "string_ordinal", "extra_field", "missing_field",
        "game_timestamp", "job_timestamp", "numeric_timestamp", "orphan_award",
        "missing_barrier_job", "missing_barrier_game", "foreign_barrier",
        "mismatched_barrier_job", "unfinished_barrier_job", "barrier_after_award",
        "barrier_before_practice", "barrier_after_window",
    ],
)
def test_habit_archive_rejects_tampered_persisted_award_evidence(
    client: TestClient,
    db_session: Session,
    tamper: str,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    award = archive["pattern_awards"][0]
    proofs = archive["pattern_award_evidence"]
    first = proofs[0]
    if tamper == "missing_row":
        proofs.pop()
    elif tamper == "missing_collection":
        archive.pop("pattern_award_evidence")
    elif tamper == "swapped_games":
        first["game_id"], proofs[1]["game_id"] = proofs[1]["game_id"], first["game_id"]
    elif tamper == "swapped_jobs":
        first["analysis_job_id"], proofs[1]["analysis_job_id"] = (
            proofs[1]["analysis_job_id"], first["analysis_job_id"],
        )
    elif tamper == "unfinished_job":
        next(row for row in archive["analysis_jobs"] if row["id"] == first["analysis_job_id"])[
            "status"
        ] = "pending"
    elif tamper == "missing_job":
        archive["analysis_jobs"] = [
            row for row in archive["analysis_jobs"] if row["id"] != first["analysis_job_id"]
        ]
    elif tamper == "out_of_order":
        for proof in proofs:
            proof["ordinal"] = 6 - proof["ordinal"]
        award["game_ids"].reverse()
        award["window_start"] = award["game_ids"][0]
        archive["pattern_targets"][0]["window_game_ids"].reverse()
    elif tamper == "duplicate_ordinal":
        proofs[1]["ordinal"] = first["ordinal"]
    elif tamper == "boolean_ordinal":
        first["ordinal"] = True
    elif tamper == "string_ordinal":
        first["ordinal"] = "1"
    elif tamper == "extra_field":
        first["policy_version"] = HABIT_POLICY_VERSION
    elif tamper == "missing_field":
        first.pop("job_completed_at")
    elif tamper == "game_timestamp":
        first["game_created_at"] = proofs[1]["game_created_at"]
    elif tamper == "job_timestamp":
        first["job_completed_at"] = proofs[1]["job_completed_at"]
    elif tamper == "numeric_timestamp":
        first["job_completed_at"] = 1
    elif tamper == "orphan_award":
        first["award_id"] = str(uuid4())
    else:
        # A short, analyzed game may release five later clean games without
        # itself being an eligible observation or one of their evidence rows.
        barrier = Game(
            pgn="",
            moves=["e2e4", "e7e5"],
            owner_player_id=PLAYER,
            owner_color="white",
            result="1/2-1/2",
            created_at=datetime.fromisoformat(first["game_created_at"]) - timedelta(hours=1),
        )
        job = AnalysisJob(
            game_id=barrier.id,
            status="completed",
            created_at=barrier.created_at,
            completed_at=datetime.fromisoformat(award["awarded_at"]),
        )
        play = PlaySession(player_id=PLAYER, game_id=barrier.id, status="completed")
        barrier_row, job_row = barrier.model_dump(mode="json"), job.model_dump(mode="json")
        award.update(barrier_game_id=str(barrier.id), barrier_analysis_job_id=str(job.id))
        if tamper == "missing_barrier_job":
            award["barrier_analysis_job_id"] = None
        elif tamper == "missing_barrier_game":
            award["barrier_game_id"] = None
        elif tamper == "foreign_barrier":
            barrier_row["owner_player_id"] = "foreign-owner"
        elif tamper == "mismatched_barrier_job":
            award["barrier_analysis_job_id"] = first["analysis_job_id"]
        elif tamper == "unfinished_barrier_job":
            job_row["status"] = "pending"
        elif tamper == "barrier_after_award":
            job_row["completed_at"] = (
                datetime.fromisoformat(award["awarded_at"]) + timedelta(hours=1)
            ).isoformat()
        elif tamper == "barrier_before_practice":
            barrier_row["created_at"] = archive["pattern_targets"][0]["started_at"]
        elif tamper == "barrier_after_window":
            barrier_row["created_at"] = (
                datetime.fromisoformat(proofs[-1]["game_created_at"]) + timedelta(minutes=1)
            ).isoformat()
        archive["games"].append(barrier_row)
        archive["analysis_jobs"].append(job_row)
        archive["play_sessions"].append(play.model_dump(mode="json"))
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert db_session.get(HabitPolicy, PLAYER) is None
    assert db_session.exec(select(PatternAwardEvidence)).all() == []


def test_short_barrier_releases_five_clean_games_and_award_roundtrips(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tests.unit.test_habit as fixtures

    archive, headers = _seed_archive(client, db_session, award=False)
    assert client.post("/v1/imports", json=archive, headers=headers).status_code == 200
    monkeypatch.setattr(fixtures, "PLAYER", PLAYER)
    target = db_session.exec(select(PatternTarget)).one()
    assert target.practice_completed_at is not None
    barrier_job = fixtures._scored_game(
        db_session, target.practice_completed_at + timedelta(hours=1),
    )
    barrier = db_session.get(Game, barrier_job.game_id)
    assert barrier is not None
    barrier.moves = barrier.moves[:2]
    db_session.add(barrier)
    for review in db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == barrier.id)
    ).all():
        db_session.delete(review)
    clean_jobs = [
        fixtures._scored_game(
            db_session, target.practice_completed_at + timedelta(hours=index + 2),
        )
        for index in range(5)
    ]
    for job in clean_jobs:
        fixtures._complete(job, db_session)
    db_session.commit()
    assert db_session.exec(select(PatternAward)).all() == []
    assert target.window_game_ids == []
    barrier_game_id, barrier_job_id = barrier.id, barrier_job.id
    clean_game_ids = [str(job.game_id) for job in clean_jobs]
    clean_job_ids = [job.id for job in clean_jobs]
    waiting = _export(client, headers)
    _delete(client, headers)
    assert client.post("/v1/imports", json=waiting, headers=headers).status_code == 200
    barrier_job = db_session.get(AnalysisJob, barrier_job_id)
    assert barrier_job is not None
    fixtures._complete(barrier_job, db_session)
    db_session.commit()
    award = db_session.exec(select(PatternAward)).one()
    assert award.barrier_game_id == barrier_game_id
    assert award.barrier_analysis_job_id == barrier_job.id
    assert award.game_ids == clean_game_ids
    proofs = db_session.exec(
        select(PatternAwardEvidence).order_by(PatternAwardEvidence.ordinal)
    ).all()
    assert [proof.ordinal for proof in proofs] == list(range(1, 6))
    assert [str(proof.game_id) for proof in proofs] == award.game_ids
    assert [proof.analysis_job_id for proof in proofs] == clean_job_ids
    awarded = _export(client, headers)
    counts = _delete(client, headers)
    assert counts["pattern_award_evidence"] == 5
    restored = client.post("/v1/imports", json=awarded, headers=headers)
    assert restored.status_code == 200, restored.text
    exported = _export(client, headers)
    assert exported["pattern_awards"] == awarded["pattern_awards"]
    assert exported["pattern_award_evidence"] == awarded["pattern_award_evidence"]


def test_habit_archive_accepts_award_after_required_analysis_completion(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    award = archive["pattern_awards"][0]
    award["awarded_at"] = (
        datetime.fromisoformat(award["awarded_at"]) + timedelta(hours=1)
    ).isoformat()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    assert _export(client, headers)["pattern_awards"] == archive["pattern_awards"]


def test_habit_archive_partial_window_does_not_reconstruct_unrecorded_games(
    client: TestClient,
    db_session: Session,
) -> None:
    archive, headers = _seed_archive(client, db_session)
    # Partial windows only assert their own ordered references. They are not a
    # replay instruction, even when other eligible games remain in the archive.
    archive["pattern_awards"] = []
    archive["pattern_award_evidence"] = []
    archive["pattern_targets"][0]["window_game_ids"] = archive["pattern_targets"][0][
        "window_game_ids"
    ][1:3]
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = _export(client, headers)
    assert restored["pattern_targets"] == archive["pattern_targets"]
    assert restored["pattern_awards"] == []
    assert restored["pattern_award_evidence"] == []


@pytest.mark.parametrize("retirement", ["active", "after_award", "before_award"])
def test_habit_archive_checks_unrecorded_recurrence_inside_persisted_span(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    retirement: str,
) -> None:
    import tests.unit.test_habit as fixtures

    archive, headers = _seed_archive(client, db_session)
    assert client.post("/v1/imports", json=archive, headers=headers).status_code == 200
    monkeypatch.setattr(fixtures, "PLAYER", PLAYER)
    award = db_session.exec(select(PatternAward)).one()
    first = db_session.get(Game, UUID(award.game_ids[0]))
    assert first is not None
    created = first.created_at + timedelta(minutes=30)
    job = fixtures._scored_game(db_session, created)
    job.status, job.created_at, job.completed_at = "completed", created, award.awarded_at
    db_session.add(job)
    source = db_session.exec(select(Position).where(Position.game_id == job.game_id)).first()
    assert source is not None
    retired_at = (
        award.awarded_at + timedelta(days=1) if retirement == "after_award"
        else award.awarded_at - timedelta(seconds=1) if retirement == "before_award"
        else None
    )
    db_session.add(ProfileObservation(
        player_id=PLAYER,
        game_id=str(job.game_id),
        position_id=str(source.id),
        skill_id=SKILL,
        observed_at=created,
        retired_at=retired_at,
    ))
    db_session.commit()
    exported = _export(client, headers)
    _delete(client, headers)
    response = client.post("/v1/imports", json=exported, headers=headers)
    assert response.status_code == (200 if retirement == "before_award" else 400), response.text
