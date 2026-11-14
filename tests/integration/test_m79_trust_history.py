from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.reports import read_player_patterns
from scan64.learning import habit
from scan64.learning.diagnosis import visibility
from scan64.learning.diagnosis.trust import HISTORICALLY_TRUSTED_CODES, TRUSTED_CODES
from tests.integration.test_habit_archive import (
    HABIT_FIELDS,
    PLAYER,
    _export,
    _seed_archive,
)

CAPTURE = "threat_processing.missed_capture"


@pytest.mark.parametrize("archived_code", [CAPTURE, "tactics.pin"])
def test_archive_uses_trust_at_award_not_current_trust(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    archived_code: str,
) -> None:
    # Construct historical data under its actual policy, then restore under demotion.
    with monkeypatch.context() as historical:
        historical.setattr(visibility, "TRUSTED_CODES", HISTORICALLY_TRUSTED_CODES)
        archive, headers = _seed_archive(client, db_session)
    active = TRUSTED_CODES - {CAPTURE}
    monkeypatch.setattr(visibility, "TRUSTED_CODES", active)
    monkeypatch.setattr(habit, "TRUSTED_CODES", active)
    if archived_code != CAPTURE:
        archive = json.loads(json.dumps(archive).replace(CAPTURE, archived_code))
    response = client.post("/v1/imports", json=archive, headers=headers)
    if archived_code != CAPTURE:
        # Historic trust is a bounded history, not permission to forge any seeded code.
        assert response.status_code == 400, response.text
        return
    assert response.status_code == 200, response.text
    restored = _export(client, headers)
    for field in HABIT_FIELDS:
        assert restored[field] == archive[field]
    assert restored["lesson_opportunities"] == archive["lesson_opportunities"]
    assert restored["skill_states"] == archive["skill_states"]
    assert all(row["trust_snapshot"]["code"] == CAPTURE for row in restored["habit_credits"])
    assert read_player_patterns(PLAYER, db_session).recurring_diagnoses == []
    projected = client.get(f"/v1/players/{PLAYER}/habit", headers=headers)
    assert projected.status_code == 200, projected.text
    target = projected.json()["targets"][0]
    assert target["status"] == "invalidated"
    assert target["invalidity_reason"] == "The tracked diagnosis is no longer trusted."
    assert target["award"]["code"] == CAPTURE
    assert len(target["award"]["evidence"]) == 5
    # Read-time withdrawal changes projections only, never award/credit snapshots.
    assert _export(client, headers) == restored
