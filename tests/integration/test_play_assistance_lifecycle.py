"""New disclosures survive supported archives without losing their assistance meaning."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.app import app
from scan64.api.models import Player
from scan64.chess.games.assistance import SessionHintReveal, prefix_hash
from scan64.chess.games.models import PlaySession
from tests.integration.test_play_assistance import OWNER, _register, _request, _witness


def _export_and_delete(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    modes: tuple[str, ...] = ("candidates",),
) -> tuple[dict[str, Any], dict[str, str], UUID]:
    headers = _register(db_session)
    play = PlaySession(
        player_id=OWNER,
        assistance_tracking_version=1,
        opponent_config={"player_color": "white", "assistance_generation": "0"},
    )
    db_session.add(play)
    db_session.commit()
    play_id = play.id
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint", AsyncMock(side_effect=_witness)
    )
    for mode in modes:
        response = _request(client, play, headers, mode=mode)
        assert response.status_code == 200, response.text
    exported = client.post("/v1/exports", headers=headers, json={"player_id": OWNER})
    assert exported.status_code == 200
    deleted = client.request(
        "DELETE",
        f"/v1/players/{OWNER}/data",
        headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{OWNER}"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["affected_rows"]["session_hint_reveals"] == 1
    return exported.json(), headers, play_id


@pytest.mark.parametrize("modes", [("candidates",), ("guard",), ("candidates", "guard")])
def test_roundtrip_and_reset_preserve_assistance_and_other_owner(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    modes: tuple[str, ...],
) -> None:
    other_headers = _register(db_session, "other-assistance-owner")
    other = PlaySession(player_id="other-assistance-owner", assistance_tracking_version=1)
    db_session.add(other)
    db_session.flush()
    db_session.add(
        SessionHintReveal(
            session_id=other.id,
            player_id=other.player_id,
            prefix_hash=prefix_hash(chess.STARTING_FEN, []),
            canonical_id=chess.Board().epd(),
            ply=1,
            initial_fen=chess.STARTING_FEN,
            moves=[],
            highest_step=0,
            guard_used=True,
        )
    )
    db_session.commit()
    other_id = other.id
    archive, headers, play_id = _export_and_delete(client, db_session, monkeypatch, modes)
    other_before = client.get(f"/v1/play-sessions/{other_id}", headers=other_headers)
    assert other_before.status_code == 200
    assert other_before.json()["assistance_modes"] == ["guard"]
    restored = client.post("/v1/imports", headers=headers, json=archive)
    assert restored.status_code == 200, restored.text
    db_session.expire_all()
    reveal = db_session.exec(
        select(SessionHintReveal).where(SessionHintReveal.session_id == play_id)
    ).one()
    assert reveal.highest_step == 0 and reveal.effective_step == 3
    assert reveal.candidate_used == ("candidates" in modes)
    assert reveal.guard_used == ("guard" in modes)
    resumed = client.get(f"/v1/play-sessions/{play_id}", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["assistance_modes"] == list(modes)
    reexported = client.post("/v1/exports", headers=headers, json={"player_id": OWNER}).json()
    assert reexported["session_hint_reveals"] == archive["session_hint_reveals"]
    reset = client.post(
        f"/v1/players/{OWNER}/progress/reset",
        headers=headers,
        json={"confirmation": "RESET PROGRESS"},
    )
    assert reset.status_code == 200, reset.text
    for _ in range(2):
        resumed = client.get(f"/v1/play-sessions/{play_id}", headers=headers)
        assert resumed.status_code == 200 and resumed.json()["assistance_modes"] == list(modes)
    db_session.expire_all()
    assert db_session.get(PlaySession, play_id) is not None
    remaining = db_session.exec(
        select(SessionHintReveal).where(SessionHintReveal.player_id == OWNER)
    ).one()
    assert remaining.model_dump(mode="json") == archive["session_hint_reveals"][0]
    assert client.get(f"/v1/play-sessions/{other_id}", headers=other_headers).json()[
        "assistance_modes"
    ] == ["guard"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_used", "false"),
        ("candidate_used", 1),
        ("candidate_used", None),
        ("guard_used", "false"),
        ("guard_used", 1),
        ("guard_used", None),
        ("highest_step", -1),
        ("highest_step", 4),
        ("candidate_used", False),
    ],
)
def test_invalid_disclosure_flags_or_empty_assistance_reject_archive_before_writes(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    archive, headers, _ = _export_and_delete(client, db_session, monkeypatch)
    archive["session_hint_reveals"][0][field] = value
    response = client.post("/v1/imports", headers=headers, json=archive)
    assert response.status_code == 400
    db_session.expire_all()
    assert db_session.get(Player, OWNER) is None
    assert db_session.exec(select(PlaySession)).all() == []
    assert db_session.exec(select(SessionHintReveal)).all() == []
