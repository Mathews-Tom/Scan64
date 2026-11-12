from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.models import Player
from scan64.content.models import ContentAttempt, ContentItem, StudySession
from scan64.content.puzzle_practice_models import (
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
    elo_after,
)
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from tests.integration.test_puzzle_lifecycle import puzzle_item

PLAYER_ID = "practice-archive-owner"
PRACTICE_FIELDS = (
    "puzzle_rating", "puzzle_daily_sets", "puzzle_daily_slots", "puzzle_rating_events",
)


def _practice_archive(
    client: TestClient, session: Session,
) -> tuple[dict[str, Any], dict[str, str]]:
    registration = client.post(
        "/v1/players", json={"id": PLAYER_ID, "display_name": "Practice owner"},
    )
    assert registration.status_code == 200
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    now = datetime(2026, 11, 13, 12, tzinfo=UTC)
    daily = PuzzleDailySet(player_id=PLAYER_ID, utc_day="2026-11-13", created_at=now)
    session.add(daily)
    session.flush()
    before = 1200.0
    for index, outcome in enumerate(("solved", "hinted", "failed", "abandoned", "unplayed")):
        item = puzzle_item()
        item.id = f"lichess:practice-{index}"
        payload = deepcopy(item.payload)
        payload.update(puzzle_id=f"practice-{index}", import_sequence=index, rating=900)
        payload["lesson"]["lesson_id"] = item.id
        item.payload = payload
        session.add(item)
        session.flush()
        slot = PuzzleDailySlot(
            player_id=PLAYER_ID, set_id=daily.id, position=index, item_id=item.id,
            item_rating=1500.0, reason="exploration", widening="400", first_exposure=True,
        )
        if outcome == "unplayed":
            session.add(slot)
            continue
        study = StudySession(player_id=PLAYER_ID, domain="puzzles")
        session.add(study)
        session.flush()
        solved = outcome in {"solved", "hinted"}
        board = chess.Board(payload["lesson"]["source"]["fen"])
        if solved:
            board.push_uci("e7e5")
        started = now + timedelta(seconds=index * 2)
        completed = started + timedelta(seconds=1)
        run = PuzzleRun(
            player_id=PLAYER_ID, item_id=item.id, session_id=study.id,
            state="solved" if solved else outcome, fen=board.fen(),
            cursor=int(solved), played_moves=["e7e5"] if solved else [],
            wrong_attempts=3 if outcome == "failed" else 0,
            hint_steps={"0": 1} if outcome == "hinted" else {},
            revision=3 if outcome == "failed" else 2 if outcome == "hinted" else 1,
            started_at=started, completed_at=completed,
        )
        session.add(run)
        session.flush()
        slot.run_id = run.id
        slot.closed_at = completed
        session.add(slot)
        session.flush()
        if outcome != "abandoned":
            session.add(ContentAttempt(
                id=run.id, item_id=item.id, player_id=PLAYER_ID, session_id=study.id,
                success=solved, hint_assisted=outcome == "hinted", completed_at=completed,
                response_payload={"run_id": run.id},
            ))
        score = int(outcome == "solved")
        after = elo_after(before, slot.item_rating, score)
        session.add(PuzzleRatingEvent(
            run_id=run.id, slot_id=slot.id, player_id=PLAYER_ID, item_id=item.id,
            sequence=index + 1, item_rating=slot.item_rating, before=before, after=after,
            score=score, created_at=completed,
        ))
        before = after
    session.add(PuzzleRating(player_id=PLAYER_ID, rating=before, rated_count=4))
    session.commit()
    exported = client.post("/v1/exports", json={"player_id": PLAYER_ID}, headers=headers)
    assert exported.status_code == 200
    archive = exported.json()
    dry_run = client.request(
        "DELETE", f"/v1/players/{PLAYER_ID}/data", headers=headers, json={"dry_run": True},
    )
    assert dry_run.status_code == 200
    counts = dry_run.json()["affected_rows"]
    assert {field: counts[field] for field in PRACTICE_FIELDS} == {
        "puzzle_rating": 1, "puzzle_daily_sets": 1,
        "puzzle_daily_slots": 5, "puzzle_rating_events": 4,
    }
    deleted = client.request(
        "DELETE", f"/v1/players/{PLAYER_ID}/data", headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{PLAYER_ID}"},
    )
    assert deleted.status_code == 200
    session.expire_all()
    return archive, headers


def _reject(
    client: TestClient, session: Session, archive: dict[str, Any], headers: dict[str, str],
) -> None:
    response = client.post(
        "/v1/imports", content=json.dumps(archive),
        headers={**headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 400, response.text
    session.expire_all()
    assert session.get(Player, PLAYER_ID) is None
    for model in (PuzzleRating, PuzzleDailySet, PuzzleDailySlot, PuzzleRatingEvent, PuzzleRun):
        assert not session.exec(select(model)).all()


def test_practice_restore_preserves_ledger_and_unplayed_dependencies_without_awards(
    client: TestClient, db_session: Session,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    assert len(archive["puzzle_content_items"]) == 5
    assert len(archive["content_attempts"]) == 3
    assert not db_session.exec(select(PuzzleRatingEvent)).all()
    # Deletion preserves the global catalog, while a fresh restore can recreate all dependencies.
    for item in db_session.exec(select(ContentItem)).all():
        db_session.delete(item)
    db_session.commit()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = client.post("/v1/exports", json={"player_id": PLAYER_ID}, headers=headers).json()
    assert restored["puzzle_rating"] == archive["puzzle_rating"]
    assert restored["puzzle_rating_events"] == archive["puzzle_rating_events"]
    assert {row["id"] for row in restored["puzzle_content_items"]} == {
        row["id"] for row in archive["puzzle_content_items"]
    }
    assert len(restored["puzzle_daily_slots"]) == 5
    assert not db_session.exec(select(SkillState)).all()
    assert not db_session.exec(select(ReviewSchedule)).all()


def test_old_puzzle_archive_imports_without_inventing_rating_history(
    client: TestClient, db_session: Session,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    for field in PRACTICE_FIELDS:
        archive.pop(field)
    # Old exports cannot include a dependency referenced only by a future M64 slot.
    archive["puzzle_content_items"] = [
        row for row in archive["puzzle_content_items"] if row["id"] != "lichess:practice-4"
    ]
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    assert db_session.get(PuzzleRating, PLAYER_ID) is None
    assert not db_session.exec(select(PuzzleRatingEvent)).all()
    assert len(db_session.exec(select(PuzzleRun)).all()) == 4


@pytest.mark.parametrize("collection,field,value", [
    ("puzzle_rating_events", "before", 1201.0),
    ("puzzle_rating_events", "after", 1300.0),
    ("puzzle_rating_events", "sequence", 2),
    ("puzzle_rating_events", "score", 0),
    ("puzzle_rating_events", "score", True),
    ("puzzle_rating_events", "item_rating", 1499.0),
    ("puzzle_rating_events", "policy_version", "future-policy"),
    ("puzzle_rating_events", "slot_id", "missing"),
    ("puzzle_rating_events", "run_id", "missing"),
    ("puzzle_rating_events", "item_id", "lichess:practice-1"),
    ("puzzle_rating_events", "player_id", "foreign"),
    ("puzzle_rating_events", "after", float("nan")),
    ("puzzle_daily_sets", "player_id", "foreign"),
    ("puzzle_daily_sets", "utc_day", "2026-11-12"),
    ("puzzle_daily_sets", "policy_version", "future-policy"),
    ("puzzle_daily_sets", "theme_map_version", "future-map"),
    ("puzzle_daily_sets", "player_rating", float("inf")),
    ("puzzle_daily_slots", "player_id", "foreign"),
    ("puzzle_daily_slots", "set_id", "missing"),
    ("puzzle_daily_slots", "item_id", "lichess:practice-1"),
    ("puzzle_daily_slots", "run_id", "missing"),
    ("puzzle_daily_slots", "closed_at", None),
    ("puzzle_daily_slots", "first_exposure", False),
    ("puzzle_daily_slots", "first_exposure", "true"),
    ("puzzle_daily_slots", "position", 2),
    ("puzzle_daily_slots", "reason", "unknown"),
    ("puzzle_daily_slots", "widening", "unknown"),
    ("puzzle_daily_slots", "item_rating", float("inf")),
    ("content_attempts", "success", False),
    ("content_attempts", "hint_assisted", True),
    ("content_attempts", "item_id", "lichess:practice-1"),
    ("content_attempts", "player_id", "foreign"),
    ("content_attempts", "session_id", "missing"),
])
def test_tampered_practice_provenance_is_rejected_atomically(
    client: TestClient, db_session: Session, collection: str, field: str, value: Any,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    # Export order need not equal slot/run/attempt order.
    if collection == "content_attempts":
        row = next(row for row in archive[collection] if row["item_id"] == "lichess:practice-0")
    elif collection == "puzzle_daily_slots":
        row = next(row for row in archive[collection] if row["position"] == 0)
    else:
        row = archive[collection][0]
    row[field] = value
    _reject(client, db_session, archive, headers)


@pytest.mark.parametrize("field,value", [
    ("rating", 1200), ("rated_count", 3), ("rated_count", True),
    ("player_id", "foreign"), ("rating", float("inf")),
])
def test_rating_must_match_complete_ordered_chain(
    client: TestClient, db_session: Session, field: str, value: Any,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    archive["puzzle_rating"][field] = value
    _reject(client, db_session, archive, headers)


@pytest.mark.parametrize("tampering", [
    "missing_event", "missing_attempt", "abandoned_attempt", "missing_slot_dependency",
    "duplicate_event", "duplicate_slot", "duplicate_puzzle", "duplicate_run_binding",
    "legacy_event", "nonterminal_event", "repeat_event", "due_event", "hinted_win",
    "failed_win", "abandoned_win", "broken_chain", "too_many_slots", "closed_unplayed",
])
def test_forged_rating_eligibility_and_membership_are_rejected(
    client: TestClient, db_session: Session, tampering: str,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    events = archive["puzzle_rating_events"]
    slots = sorted(archive["puzzle_daily_slots"], key=lambda slot: slot["position"])
    runs = {run["id"]: run for run in archive["puzzle_runs"]}
    if tampering == "missing_event":
        events.pop()
    elif tampering == "missing_attempt":
        archive["content_attempts"] = [
            row for row in archive["content_attempts"] if row["id"] != events[0]["run_id"]
        ]
    elif tampering == "abandoned_attempt":
        attempt = deepcopy(archive["content_attempts"][0])
        run = runs[events[3]["run_id"]]
        attempt.update(id=run["id"], item_id=run["item_id"], session_id=run["session_id"])
        archive["content_attempts"].append(attempt)
    elif tampering == "missing_slot_dependency":
        archive["puzzle_content_items"] = [
            row for row in archive["puzzle_content_items"] if row["id"] != slots[4]["item_id"]
        ]
    elif tampering == "duplicate_event":
        events.append(deepcopy(events[0]))
    elif tampering == "duplicate_slot":
        archive["puzzle_daily_slots"].append(deepcopy(slots[0]))
    elif tampering == "duplicate_puzzle":
        slots[4]["item_id"] = slots[0]["item_id"]
    elif tampering == "duplicate_run_binding":
        daily = deepcopy(archive["puzzle_daily_sets"][0])
        daily.update(id="second-set", utc_day="2026-11-14", created_at="2026-11-14T00:00:00")
        archive["puzzle_daily_sets"].append(daily)
        slot = deepcopy(slots[0])
        slot.update(id="second-slot", set_id=daily["id"], position=0, first_exposure=False)
        archive["puzzle_daily_slots"].append(slot)
    elif tampering == "legacy_event":
        slots[0].update(run_id=None, closed_at=None)
    elif tampering == "nonterminal_event":
        run = runs[events[3]["run_id"]]
        run.update(state="active", completed_at=None)
        slots[3]["closed_at"] = None
    elif tampering == "repeat_event":
        run = deepcopy(runs[events[0]["run_id"]])
        study = deepcopy(next(
            row for row in archive["study_sessions"] if row["id"] == run["session_id"]
        ))
        study["id"] = "legacy-study"
        archive["study_sessions"].append(study)
        board = chess.Board(puzzle_item().payload["lesson"]["source"]["fen"])
        run.update(
            id="legacy-run", session_id=study["id"], state="abandoned", fen=board.fen(),
            cursor=0, played_moves=[], started_at="2026-11-12T12:00:00",
            completed_at="2026-11-12T12:00:01",
        )
        archive["puzzle_runs"].append(run)
    elif tampering == "due_event":
        slots[0].update(reason="due", widening="due", first_exposure=False)
    elif tampering in {"hinted_win", "failed_win", "abandoned_win"}:
        index = {"hinted_win": 1, "failed_win": 2, "abandoned_win": 3}[tampering]
        events[index]["score"] = 1
    elif tampering == "broken_chain":
        events[1]["before"] += 0.25
    elif tampering == "too_many_slots":
        for position in range(5, 11):
            item = deepcopy(archive["puzzle_content_items"][0])
            item["id"] = f"lichess:extra-{position}"
            item["payload"]["puzzle_id"] = f"extra-{position}"
            item["payload"]["lesson"]["lesson_id"] = item["id"]
            archive["puzzle_content_items"].append(item)
            slot = deepcopy(slots[4])
            slot.update(id=f"extra-slot-{position}", item_id=item["id"], position=position)
            archive["puzzle_daily_slots"].append(slot)
    else:
        assert tampering == "closed_unplayed"
        slots[4]["closed_at"] = archive["puzzle_daily_sets"][0]["created_at"]
    _reject(client, db_session, archive, headers)


def test_pending_first_exposure_restores_without_fabricating_terminal_award(
    client: TestClient, db_session: Session,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    pending = archive["puzzle_rating_events"].pop()
    run = next(row for row in archive["puzzle_runs"] if row["id"] == pending["run_id"])
    run.update(state="active", completed_at=None, hint_steps={"0": 1}, revision=2)
    slot = next(row for row in archive["puzzle_daily_slots"] if row["id"] == pending["slot_id"])
    slot["closed_at"] = None
    archive["puzzle_rating"].update(rating=pending["before"], rated_count=3)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    restored = db_session.get(PuzzleRun, run["id"])
    assert restored is not None and restored.state == "active"
    assert restored.hint_steps == {"0": 1}
    assert db_session.get(PuzzleRatingEvent, run["id"]) is None
    assert db_session.get(ContentAttempt, run["id"]) is None


def test_restore_does_not_compare_source_snapshot_to_mutable_catalog_rating(
    client: TestClient, db_session: Session,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    item = db_session.get(ContentItem, "lichess:practice-0")
    assert item is not None
    item.payload = {**item.payload, "rating": 2300}
    db_session.add(item)
    db_session.commit()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    item = db_session.get(ContentItem, "lichess:practice-0")
    assert item is not None and item.payload["rating"] == 2300
    event = db_session.get(PuzzleRatingEvent, archive["puzzle_rating_events"][0]["run_id"])
    assert event is not None and event.item_rating == 1500


def test_deletion_keeps_other_players_practice_and_global_catalog(
    client: TestClient, db_session: Session,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    registration = client.post(
        "/v1/players", json={"id": "other-practice", "display_name": "Other"}
    )
    assert registration.status_code == 200
    daily = PuzzleDailySet(player_id="other-practice", utc_day="2026-11-13")
    db_session.add(daily)
    db_session.add(PuzzleRating(player_id="other-practice"))
    db_session.flush()
    slot = PuzzleDailySlot(
        player_id="other-practice", set_id=daily.id, position=0,
        item_id="lichess:practice-4", item_rating=1500, reason="exploration",
        widening="400", first_exposure=True,
    )
    db_session.add(slot)
    db_session.commit()
    daily_id, slot_id = daily.id, slot.id
    assert client.post("/v1/imports", json=archive, headers=headers).status_code == 200
    deleted = client.request(
        "DELETE", f"/v1/players/{PLAYER_ID}/data", headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{PLAYER_ID}"},
    )
    assert deleted.status_code == 200
    db_session.expire_all()
    assert db_session.get(PuzzleRating, "other-practice") is not None
    assert db_session.get(PuzzleDailySet, daily_id) is not None
    assert db_session.get(PuzzleDailySlot, slot_id) is not None
    for item in archive["puzzle_content_items"]:
        assert db_session.get(ContentItem, item["id"]) is not None
    assert db_session.get(PuzzleRating, PLAYER_ID) is None
    assert not db_session.exec(
        select(PuzzleDailySet).where(PuzzleDailySet.player_id == PLAYER_ID)
    ).all()
    assert not db_session.exec(
        select(PuzzleDailySlot).where(PuzzleDailySlot.player_id == PLAYER_ID)
    ).all()
    assert not db_session.exec(
        select(PuzzleRatingEvent).where(PuzzleRatingEvent.player_id == PLAYER_ID)
    ).all()


def test_due_repeat_restores_unrated_without_replaying_first_exposure(
    client: TestClient, db_session: Session,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    source = archive["puzzle_rating_events"][0]
    original = next(row for row in archive["puzzle_runs"] if row["id"] == source["run_id"])
    study = deepcopy(next(
        row for row in archive["study_sessions"] if row["id"] == original["session_id"]
    ))
    study["id"] = "review-study"
    archive["study_sessions"].append(study)
    run = deepcopy(original)
    run.update(
        id="review-run", session_id=study["id"], started_at="2026-11-14T12:00:00",
        completed_at="2026-11-14T12:00:01",
    )
    archive["puzzle_runs"].append(run)
    attempt = deepcopy(next(
        row for row in archive["content_attempts"] if row["id"] == original["id"]
    ))
    attempt.update(
        id=run["id"], session_id=study["id"], completed_at=run["completed_at"],
        started_at=run["started_at"],
        response_payload={"run_id": run["id"]},
    )
    archive["content_attempts"].append(attempt)
    daily = deepcopy(archive["puzzle_daily_sets"][0])
    daily.update(
        id="review-set", utc_day="2026-11-14", created_at="2026-11-14T00:00:00",
        player_rating=archive["puzzle_rating"]["rating"],
    )
    archive["puzzle_daily_sets"].append(daily)
    slot = deepcopy(next(
        row for row in archive["puzzle_daily_slots"] if row["id"] == source["slot_id"]
    ))
    slot.update(
        id="review-slot", set_id=daily["id"], position=0, first_exposure=False,
        reason="due", widening="due", run_id=run["id"], closed_at=run["completed_at"],
    )
    archive["puzzle_daily_slots"].append(slot)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(PuzzleRatingEvent, run["id"]) is None
    rating = db_session.get(PuzzleRating, PLAYER_ID)
    assert rating is not None and rating.rated_count == 4


def test_mathematically_valid_chain_cannot_start_above_initial_rating(
    client: TestClient, db_session: Session,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    before = 1250.0
    for event in archive["puzzle_rating_events"]:
        event["before"] = before
        event["after"] = elo_after(before, event["item_rating"], event["score"])
        before = event["after"]
    archive["puzzle_rating"]["rating"] = before
    _reject(client, db_session, archive, headers)


@pytest.mark.parametrize("field,value", [
    ("rating", "not-a-rating"), ("rating", "1500"), ("rating", None),
    ("rating", True), ("rating", []), ("rating", {}), ("rating", 10**400),
    ("rating", float("nan")), ("rating", float("inf")),
    ("difficulty_estimate", "not-a-rating"), ("difficulty_estimate", "1500"),
    ("difficulty_estimate", None), ("difficulty_estimate", True),
    ("difficulty_estimate", float("nan")), ("difficulty_estimate", float("inf")),
    ("themes", None), ("themes", "mateIn1"), ("themes", {"mateIn1": True}),
    ("themes", [{}]), ("themes", [["mateIn1"]]), ("themes", [1]),
])
def test_unplayed_dependency_cannot_poison_other_players_practice(
    client: TestClient, db_session: Session, field: str, value: Any,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    poisoned_id = "lichess:practice-4"
    dependency = next(
        row for row in archive["puzzle_content_items"] if row["id"] == poisoned_id
    )
    if field == "difficulty_estimate":
        dependency[field] = value
    else:
        dependency["payload"][field] = value
    existing = db_session.get(ContentItem, poisoned_id)
    assert existing is not None
    db_session.delete(existing)
    db_session.commit()

    _reject(client, db_session, archive, headers)
    assert db_session.get(ContentItem, poisoned_id) is None
    registration = client.post(
        "/v1/players", json={"id": "poisoning-bystander", "display_name": "Bystander"}
    )
    assert registration.status_code == 200
    other_headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    daily = client.get("/v1/puzzles/daily", headers=other_headers)
    assert daily.status_code == 200, daily.text
    assert all(
        slot.item_id != poisoned_id for slot in db_session.exec(
            select(PuzzleDailySlot).where(PuzzleDailySlot.player_id == "poisoning-bystander")
        ).all()
    )


def test_legacy_dependency_rating_uses_validated_difficulty_fallback(
    client: TestClient, db_session: Session,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    for dependency in archive["puzzle_content_items"]:
        dependency["payload"].pop("rating")
        dependency["difficulty_estimate"] = 1500.0
        existing = db_session.get(ContentItem, dependency["id"])
        assert existing is not None
        db_session.delete(existing)
    db_session.commit()
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    registration = client.post(
        "/v1/players", json={"id": "fallback-bystander", "display_name": "Bystander"}
    )
    assert registration.status_code == 200
    other_headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    daily = client.get("/v1/puzzles/daily", headers=other_headers)
    assert daily.status_code == 200, daily.text
    slots = db_session.exec(
        select(PuzzleDailySlot).where(PuzzleDailySlot.player_id == "fallback-bystander")
    ).all()
    assert slots
    assert all(slot.item_rating == 1500.0 for slot in slots)


@pytest.mark.parametrize("difficulty", [None, True, "1500", float("inf"), 10**400])
def test_legacy_dependency_fallback_must_be_finite_numeric(
    client: TestClient, db_session: Session, difficulty: Any,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    dependency = next(
        row for row in archive["puzzle_content_items"] if row["id"] == "lichess:practice-4"
    )
    dependency["payload"].pop("rating")
    if difficulty is None:
        dependency.pop("difficulty_estimate")
    else:
        dependency["difficulty_estimate"] = difficulty
    _reject(client, db_session, archive, headers)


@pytest.mark.parametrize("completed_at", [None, "2026-11-12T12:00:01"])
def test_earlier_legacy_attempt_without_run_cannot_be_rated_as_first_exposure(
    client: TestClient, db_session: Session, completed_at: str | None,
) -> None:
    archive, headers = _practice_archive(client, db_session)
    event = archive["puzzle_rating_events"][0]
    attempt = deepcopy(next(
        row for row in archive["content_attempts"] if row["id"] == event["run_id"]
    ))
    attempt.update(
        id="earlier-legacy-attempt", started_at="2026-11-12T12:00:00",
        completed_at=completed_at, response_payload={},
    )
    archive["content_attempts"].append(attempt)
    _reject(client, db_session, archive, headers)
