from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from scan64.learning.habit_models import HabitGoal, HabitPolicy, PatternTarget


def _owner(client: TestClient, player_id: str = "habit-api-owner") -> dict[str, str]:
    response = client.post("/v1/players", json={"id": player_id})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_habit_read_has_no_writes_and_new_player_policy_is_prospective(
    client: TestClient, db_session: Session
) -> None:
    before = datetime.now(UTC)
    headers = _owner(client)
    policy = db_session.get(HabitPolicy, "habit-api-owner")
    assert policy is not None
    assert policy.effective_at.replace(tzinfo=UTC) >= before
    statements: list[str] = []

    def record_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(db_session.get_bind(), "before_cursor_execute", record_statement)
    try:
        response = client.get("/v1/players/habit-api-owner/habit", headers=headers)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record_statement)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["goal"] == 3 and data["qualifying_solves"] == 0 and data["remaining"] == 3
    assert data["day_status"] == "in_progress"
    assert data["current_streak"] == 0 and data["best_streak"] == 0
    assert not {"INSERT", "UPDATE", "DELETE"} & set(statements)


def test_goal_edits_are_positive_strict_and_effective_next_utc_day(
    client: TestClient, db_session: Session
) -> None:
    headers = _owner(client)
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    for invalid in (0, -1, True, 1.5, "4", 2**63):
        response = client.post(
            "/v1/players/habit-api-owner/habit/goal", headers=headers, json={"goal": invalid}
        )
        assert response.status_code == 422
    response = client.post(
        "/v1/players/habit-api-owner/habit/goal", headers=headers, json={"goal": 5}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["goal"] == 3 and data["remaining"] == 3
    assert data["pending_goal"] == 5 and data["pending_effective_day"] == tomorrow
    edited = client.post(
        "/v1/players/habit-api-owner/habit/goal", headers=headers, json={"goal": 2}
    )
    assert edited.status_code == 200
    rows = db_session.exec(select(HabitGoal)).all()
    assert len(rows) == 1 and rows[0].effective_day == tomorrow and rows[0].goal == 2


@pytest.mark.parametrize(
    "suffix,body",
    [
        ("", None),
        ("/goal", {"goal": 1}),
        ("/targets", {"code": "board_awareness.hanging_piece"}),
        ("/targets/missing/refresh", {}),
    ],
)
def test_habit_routes_reject_foreign_and_missing_tokens(
    client: TestClient, suffix: str, body: dict | None
) -> None:
    _owner(client)
    foreign = _owner(client, "habit-api-other")
    url = f"/v1/players/habit-api-owner/habit{suffix}"
    if body is None:
        absent = client.get(url)
        denied = client.get(url, headers=foreign)
    else:
        absent = client.post(url, json=body)
        denied = client.post(url, headers=foreign, json=body)
    assert absent.status_code == 401 and denied.status_code == 404


def test_target_cannot_be_farmed_from_theme_or_arbitrary_code(
    client: TestClient, db_session: Session
) -> None:
    headers = _owner(client)
    for code in ("hangingPiece", "board_awareness.hanging_piece", "made.up"):
        response = client.post(
            "/v1/players/habit-api-owner/habit/targets", headers=headers, json={"code": code}
        )
        assert response.status_code == 409, response.text
    assert db_session.exec(select(PatternTarget)).all() == []
    assert (
        client.post(
            "/v1/players/habit-api-owner/habit/targets/missing/refresh", headers=headers, json={}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("write", ["refresh", "start"])
def test_owned_target_is_idempotent_and_withdrawal_is_read_only_until_explicit_write(
    client: TestClient, db_session: Session, write: str
) -> None:
    from tests.integration.test_reports_from_real_data import (
        create_report_game,
        create_report_lesson,
    )

    headers = _owner(client)
    opportunities = []
    code = "threat_processing.missed_capture"
    for _ in range(3):
        game = create_report_game(db_session, "habit-api-owner")
        opportunity = create_report_lesson(db_session, game, code, with_review=True)
        opportunity.lesson_spec = {
            **opportunity.lesson_spec,
            "source": {**opportunity.lesson_spec["source"], "kind": "player_game"},
        }
        db_session.add(opportunity)
        opportunities.append(opportunity)
    db_session.commit()
    path = "/v1/players/habit-api-owner/habit"
    first = client.post(f"{path}/targets", headers=headers, json={"code": code})
    assert first.status_code == 200, first.text
    target_id = first.json()["targets"][0]["id"]
    repeated = client.post(f"{path}/targets", headers=headers, json={"code": code})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["targets"][0]["id"] == target_id
    target = db_session.get(PatternTarget, target_id)
    assert target is not None and target.code == code and target.invalidated_at is None
    opportunity = opportunities[0]
    opportunity.verification_status = "invalid"
    db_session.add(opportunity)
    db_session.commit()
    projected = client.get(path, headers=headers)
    assert projected.status_code == 200, projected.text
    assert projected.json()["targets"][0]["invalidity_reason"]
    db_session.refresh(target)
    assert target.invalidated_at is None
    if write == "refresh":
        persisted = client.post(f"{path}/targets/{target_id}/refresh", headers=headers, json={})
        assert persisted.status_code == 200, persisted.text
    else:
        persisted = client.post(f"{path}/targets", headers=headers, json={"code": code})
        assert persisted.status_code == 409, persisted.text
    db_session.refresh(target)
    assert target.invalidated_at is not None and target.invalidation_reason
