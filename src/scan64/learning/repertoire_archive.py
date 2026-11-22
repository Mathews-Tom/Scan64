"""Validate an entire repertoire archive graph without fallback to retained owners."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, SQLModel

from scan64.learning.repertoire import (
    MAX_FEN_LENGTH,
    MAX_NODES,
    MAX_PGN_BYTES,
    Repertoire,
    RepertoireAttempt,
    RepertoireTree,
    parse_repertoire_pgn,
    repertoire_item_id,
    validate_repertoire_fen,
    verify_repertoire_move,
)
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule, ScheduleReviewObservation


def _utc(value: datetime) -> datetime:
    try:
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise ValueError("Invalid repertoire timestamp") from error
    if not datetime(1970, 1, 1, tzinfo=UTC) <= normalized < datetime(2200, 1, 1, tzinfo=UTC):
        raise ValueError("Repertoire timestamp is outside the supported range")
    return normalized


def repertoire_review_time(value: datetime) -> datetime:
    normalized = _utc(value)
    if normalized >= datetime(2100, 1, 1, tzinfo=UTC):
        raise ValueError("Repertoire review time must be before 2100")
    return normalized


def _archive_time(value: Any, now: datetime, *, due: bool = False) -> None:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Repertoire timestamps must be bounded ISO strings")
    try:
        parsed = _utc(datetime.fromisoformat(value))
    except (ValueError, OverflowError) as error:
        raise ValueError("Invalid repertoire timestamp") from error
    if not due and (
        parsed >= datetime(2100, 1, 1, tzinfo=UTC) or parsed > now + timedelta(minutes=5)
    ):
        raise ValueError("Repertoire timestamps may be at most five minutes in the future")


def validate_repertoire_archive_inputs(
    repertoires: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    schedules: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> None:
    """Bound only new records before model/tree copies or FSRS replay.

    Archives can contain any number of independently imported repertoires. No
    total archive/body limit is imposed on the supported legacy restore format.
    """
    now = datetime.now(UTC)
    for data in repertoires:
        pgn, title, tree = data.get("pgn"), data.get("title"), data.get("tree")
        if (
            not isinstance(pgn, str)
            or len(pgn) > MAX_PGN_BYTES
            or len(pgn.encode("utf-8")) > MAX_PGN_BYTES
            or not isinstance(title, str)
            or not 1 <= len(title) <= 200
            or not isinstance(tree, dict)
            or set(tree) != {"nodes"}
            or not isinstance(tree["nodes"], list)
            or not 1 < len(tree["nodes"]) <= MAX_NODES
        ):
            raise ValueError("Repertoire archive exceeds the supported record bounds")
        edges = 0
        for node in tree["nodes"]:
            if not isinstance(node, dict) or set(node) != {
                "id",
                "parent",
                "fen",
                "uci",
                "san",
                "children",
            }:
                raise ValueError("Invalid repertoire node")
            fen, children = node.get("fen"), node.get("children")
            if (
                type(node.get("id")) is not int
                or not 0 <= node["id"] < MAX_NODES
                or (
                    node.get("parent") is not None
                    and (type(node["parent"]) is not int or not 0 <= node["parent"] < MAX_NODES)
                )
                or not isinstance(fen, str)
                or len(fen) > MAX_FEN_LENGTH
                or not isinstance(children, list)
                or len(children) >= MAX_NODES
                or any(type(child) is not int or not 0 <= child < MAX_NODES for child in children)
                or (
                    node.get("uci") is not None
                    and (not isinstance(node["uci"], str) or not 4 <= len(node["uci"]) <= 5)
                )
                or (
                    node.get("san") is not None
                    and (not isinstance(node["san"], str) or len(node["san"]) > 32)
                )
            ):
                raise ValueError("Repertoire node exceeds the supported bounds")
            validate_repertoire_fen(fen)
            edges += len(children)
            if edges >= MAX_NODES:
                raise ValueError("Repertoire tree has too many continuations")
        _archive_time(data.get("created_at"), now)
    for data in attempts:
        if (
            type(data.get("node_id")) is not int
            or not 0 <= data["node_id"] < MAX_NODES
            or type(data.get("sequence")) is not int
            or not 1 <= data["sequence"] < 2**63
            or not isinstance(data.get("submitted_move"), str)
            or not 4 <= len(data["submitted_move"]) <= 5
        ):
            raise ValueError("Repertoire attempt exceeds the supported bounds")
        _archive_time(data.get("completed_at"), now)
    for data in schedules:
        item_id = data.get("item_id")
        if isinstance(item_id, str) and item_id.startswith("repertoire:"):
            _archive_time(data.get("next_review_at"), now, due=True)
            for key in ("last_reviewed_at", "retired_at"):
                if data.get(key) is not None:
                    _archive_time(data[key], now)
    for data in observations:
        if data.get("source_kind") == "repertoire":
            _archive_time(data.get("reviewed_at"), now)


def _complete[Row: SQLModel](model: type[Row], data: dict[str, Any]) -> Row:
    if set(data) != set(model.model_fields):
        raise ValueError("Incomplete or unknown repertoire archive fields")
    return model.model_validate(data)


def archive_repertoire(data: dict[str, Any]) -> Repertoire:
    return _complete(Repertoire, data)


def archive_repertoire_attempt(data: dict[str, Any]) -> RepertoireAttempt:
    if type(data.get("node_id")) is not int or type(data.get("sequence")) is not int:
        raise ValueError("Invalid repertoire decision identity")
    if type(data.get("success")) is not bool:
        raise ValueError("Invalid repertoire result")
    attempt = _complete(RepertoireAttempt, data)
    attempt.verification_status = "historical"
    return attempt


def validate_repertoire_archive(
    session: Session,
    player_id: str,
    repertoires: list[Repertoire],
    attempts: list[RepertoireAttempt],
    schedules: list[ReviewSchedule],
    observations: list[ScheduleReviewObservation],
) -> None:
    by_id = {repertoire.id: repertoire for repertoire in repertoires}
    by_attempt = {str(attempt.id): attempt for attempt in attempts}
    if len(by_id) != len(repertoires) or len(by_attempt) != len(attempts):
        raise ValueError("Duplicate repertoire archive identity")
    for repertoire in repertoires:
        if repertoire.player_id != player_id or session.get(Repertoire, repertoire.id) is not None:
            raise ValueError("Repertoire conflicts with an existing owner or identity")
        parsed = parse_repertoire_pgn(repertoire.pgn)
        RepertoireTree.model_validate(repertoire.tree)
        if len(parsed) != 1 or parsed[0][2].model_dump() != repertoire.tree:
            raise ValueError("Repertoire tree does not match its complete legal PGN")
    if any(
        row.item_id.startswith("repertoire:") and row.source_kind != "repertoire"
        for row in observations
    ):
        raise ValueError("Repertoire schedules require repertoire review provenance")
    review_rows = [row for row in observations if row.source_kind == "repertoire"]
    by_source = {row.source_id: row for row in review_rows}
    if len(by_source) != len(review_rows) or by_source.keys() != by_attempt.keys():
        raise ValueError("Every repertoire attempt must have exactly one matching review")
    replayed: dict[str, ReviewSchedule] = {}
    sequences: dict[str, int] = {}
    for attempt in sorted(
        attempts, key=lambda row: (str(row.repertoire_id), row.node_id, row.sequence)
    ):
        attempt_repertoire = by_id.get(attempt.repertoire_id)
        if (
            attempt.player_id != player_id
            or attempt_repertoire is None
            or session.get(RepertoireAttempt, attempt.id) is not None
            or _utc(attempt.completed_at) < _utc(attempt_repertoire.created_at)
        ):
            raise ValueError("Repertoire attempt is missing its owned source")
        success = verify_repertoire_move(
            attempt_repertoire, attempt.node_id, attempt.submitted_move
        )
        item_id = repertoire_item_id(attempt_repertoire.id, attempt.node_id)
        sequences[item_id] = sequences.get(item_id, 0) + 1
        if attempt.sequence != sequences[item_id] or attempt.success is not success:
            raise ValueError("Repertoire attempt does not match its saved continuation or sequence")
        observation = by_source[str(attempt.id)]
        if (
            observation.player_id != player_id
            or observation.item_id != item_id
            or observation.success is not success
            or observation.hints_used is not None
            or observation.elapsed_ms is not None
            or observation.input_provenance != "server_recorded"
            or _utc(observation.reviewed_at) != _utc(attempt.completed_at)
        ):
            raise ValueError("Repertoire review does not match its server-recorded attempt")
        schedule = replayed.get(item_id)
        if schedule is None:
            schedule = ReviewSchedule(
                player_id=player_id,
                item_id=item_id,
                next_review_at=_utc(attempt.completed_at),
            )
            replayed[item_id] = schedule
        elif schedule.last_reviewed_at is not None and (
            _utc(attempt.completed_at) < _utc(schedule.last_reviewed_at)
        ):
            raise ValueError("Repertoire attempt order is not chronological")
        expected = schedule.update(
            success,
            _utc(attempt.completed_at),
            hints_used=None,
            elapsed_ms=None,
            source_kind="repertoire",
            source_id=str(attempt.id),
        )
        if expected.rating != observation.rating:
            raise ValueError("Repertoire rating does not match its review inputs")
    repertoire_schedules = {
        row.item_id: row for row in schedules if row.item_id.startswith("repertoire:")
    }
    if repertoire_schedules.keys() != replayed.keys():
        raise ValueError("Repertoire schedule is missing its complete attempt history")
    for item_id, expected_schedule in replayed.items():
        actual = repertoire_schedules[item_id]
        for name in ReviewSchedule.model_fields:
            expected_value = getattr(expected_schedule, name)
            actual_value = getattr(actual, name)
            if isinstance(expected_value, datetime) and isinstance(actual_value, datetime):
                expected_value, actual_value = _utc(expected_value), _utc(actual_value)
            if actual_value != expected_value:
                raise ValueError("Repertoire schedule disagrees with recorded FSRS reviews")
    # SQLite's datetime storage drops offsets. Persist the validated instants
    # as UTC, not their original local wall-clock representations.
    for repertoire in repertoires:
        repertoire.created_at = _utc(repertoire.created_at)
    for attempt in attempts:
        attempt.completed_at = _utc(attempt.completed_at)
    for schedule in repertoire_schedules.values():
        schedule.next_review_at = _utc(schedule.next_review_at)
        if schedule.last_reviewed_at is not None:
            schedule.last_reviewed_at = _utc(schedule.last_reviewed_at)
        if schedule.retired_at is not None:
            schedule.retired_at = _utc(schedule.retired_at)
    for observation in review_rows:
        observation.reviewed_at = _utc(observation.reviewed_at)
