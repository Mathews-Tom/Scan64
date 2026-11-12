from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.models import Player
from scan64.content.models import ContentAttempt, ContentItem, StudySession
from scan64.content.puzzle_runs import PuzzleRun


def puzzle_item() -> ContentItem:
    board = chess.Board()
    board.push_uci("e2e4")
    return ContentItem(
        id="lichess:lifecycle",
        domain="puzzles",
        version="0.2.0",
        provenance="Lichess puzzle database",
        licence="CC0-1.0",
        skill_mapping={},
        payload={
            "puzzle_id": "lifecycle",
            "fen": chess.STARTING_FEN,
            "moves": ["e2e4", "e7e5"],
            "setup": "e2e4",
            "import_sequence": 1,
            "lesson": {
                "schema_version": "0.2.0",
                "lesson_id": "lichess:lifecycle",
                "source": {"kind": "external", "fen": board.fen()},
                "objective": {"type": "puzzle", "instruction": "Find the move"},
                "interaction": {"input": "move", "maximum_attempts": 3, "solution": ["e7e5"]},
                "verification": {"status": "source_verified"},
            },
        },
    )


def seed_puzzle_run(session: Session, player_id: str, *, solved: bool = False) -> PuzzleRun:
    item = session.get(ContentItem, "lichess:lifecycle")
    if item is None:
        item = puzzle_item()
        session.add(item)
    study = StudySession(player_id=player_id, domain="puzzles")
    session.add(study)
    session.flush()
    board = chess.Board(item.payload["lesson"]["source"]["fen"])
    if solved:
        board.push_uci("e7e5")
    now = datetime.now(UTC)
    run = PuzzleRun(
        player_id=player_id,
        item_id=item.id,
        session_id=study.id,
        fen=board.fen(),
        state="solved" if solved else "active",
        cursor=1 if solved else 0,
        revision=1 if solved else 0,
        played_moves=["e7e5"] if solved else [],
        started_at=now,
        completed_at=now if solved else None,
    )
    session.add(run)
    session.flush()
    if solved:
        session.add(
            ContentAttempt(
                id=run.id,
                item_id=item.id,
                player_id=player_id,
                session_id=study.id,
                success=True,
                completed_at=now,
                response_payload={"run_id": run.id},
            )
        )
    session.commit()
    return run


def _archive(
    client: TestClient, session: Session, *, solved: bool = True
) -> tuple[dict[str, Any], dict[str, str]]:
    registration = client.post(
        "/v1/players", json={"id": "puzzle-owner", "display_name": "Puzzle owner"}
    )
    assert registration.status_code == 200
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    seed_puzzle_run(session, "puzzle-owner", solved=solved)
    response = client.post("/v1/exports", json={"player_id": "puzzle-owner"}, headers=headers)
    assert response.status_code == 200
    archive = response.json()
    deletion = client.request(
        "DELETE",
        "/v1/players/puzzle-owner/data",
        headers=headers,
        json={"dry_run": False, "confirmation": "delete-puzzle-owner"},
    )
    assert deletion.status_code == 200
    session.expire_all()
    return archive, headers


def test_fresh_restore_inserts_puzzle_dependencies(client: TestClient, db_session: Session) -> None:
    archive, headers = _archive(client, db_session)
    item = db_session.get(ContentItem, "lichess:lifecycle")
    assert item is not None
    db_session.delete(item)
    db_session.commit()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(ContentItem, "lichess:lifecycle") is not None
    assert db_session.get(PuzzleRun, archive["puzzle_runs"][0]["id"]) is not None
    assert db_session.get(ContentAttempt, archive["content_attempts"][0]["id"]) is not None


def test_restore_keeps_existing_global_metadata(client: TestClient, db_session: Session) -> None:
    archive, headers = _archive(client, db_session)
    archive["puzzle_content_items"][0]["provenance"] = "Different archive metadata"
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    item = db_session.get(ContentItem, "lichess:lifecycle")
    assert item is not None and item.provenance == "Lichess puzzle database"


def test_identity_conflict_is_atomic(client: TestClient, db_session: Session) -> None:
    archive, headers = _archive(client, db_session)
    item = db_session.get(ContentItem, "lichess:lifecycle")
    assert item is not None
    item.payload = {**item.payload, "moves": ["d2d4", "d7d5"]}
    db_session.add(item)
    db_session.commit()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400
    assert db_session.get(Player, "puzzle-owner") is None
    assert not db_session.exec(select(PuzzleRun)).all()


@pytest.mark.parametrize(
    "field,value",
    [
        ("fen", chess.STARTING_FEN),
        ("cursor", 2),
        ("revision", -1),
        ("wrong_attempts", 3),
        ("hint_steps", {"1": 1}),
        ("played_moves", ["e7e6"]),
        ("player_id", "foreign"),
        ("session_id", "foreign"),
        ("state", "solved"),
    ],
)
def test_tampered_resume_is_rejected(
    client: TestClient, db_session: Session, field: str, value: Any
) -> None:
    archive, headers = _archive(client, db_session, solved=False)
    archive["puzzle_runs"][0][field] = value
    assert client.post("/v1/imports", json=archive, headers=headers).status_code == 400
    assert db_session.get(Player, "puzzle-owner") is None


def test_old_archive_without_puzzle_fields(client: TestClient, db_session: Session) -> None:
    archive, headers = _archive(client, db_session)
    archive.pop("puzzle_runs")
    archive.pop("puzzle_content_items")
    archive["content_attempts"] = []
    assert client.post("/v1/imports", json=archive, headers=headers).status_code == 200


def test_export_and_delete_preserve_other_players_shared_puzzle(
    client: TestClient, db_session: Session
) -> None:
    registration = client.post("/v1/players", json={"id": "other", "display_name": "Other"})
    assert registration.status_code == 200
    other = seed_puzzle_run(db_session, "other")
    other_id = other.id
    archive, _ = _archive(client, db_session)
    assert {row["player_id"] for row in archive["puzzle_runs"]} == {"puzzle-owner"}
    assert len(archive["puzzle_content_items"]) == 1
    assert db_session.get(PuzzleRun, other_id) is not None
    assert db_session.get(ContentItem, "lichess:lifecycle") is not None


def test_catalog_cannot_restore_arbitrary_global_content(
    client: TestClient, db_session: Session
) -> None:
    archive, headers = _archive(client, db_session)
    forged = deepcopy(archive)
    forged["puzzle_content_items"][0]["domain"] = "tactics"
    assert client.post("/v1/imports", json=forged, headers=headers).status_code == 400


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("source", "kind", "player_game"),
        ("source", "fen", chess.STARTING_FEN),
        ("interaction", "solution", ["e7e6"]),
        ("interaction", "accepted_moves", [{"san": "e5"}]),
        ("verification", "engine", "forged-engine"),
    ],
)
def test_forged_lesson_is_rejected(
    client: TestClient, db_session: Session, section: str, field: str, value: Any
) -> None:
    archive, headers = _archive(client, db_session)
    archive["puzzle_content_items"][0]["payload"]["lesson"][section][field] = value
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400
    assert db_session.get(Player, "puzzle-owner") is None
