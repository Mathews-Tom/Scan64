from datetime import UTC, datetime

from scan64.content import ContentAttempt, ContentItem, StudySession


def test_content_item_creation():
    item = ContentItem(
        domain="tactics",
        payload={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
        provenance="Lichess Puzzle DB",
        licence="CC0",
        skill_mapping={"tactics.fork": 1.0, "tactics.pin": 0.5},
        difficulty_estimate=1200.0,
    )

    assert item.id is not None
    assert item.domain == "tactics"
    assert item.version == "1.0"
    assert item.payload["fen"] == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    assert item.provenance == "Lichess Puzzle DB"
    assert item.licence == "CC0"
    assert "tactics.fork" in item.skill_mapping
    assert item.skill_mapping["tactics.pin"] == 0.5
    assert item.difficulty_estimate == 1200.0


def test_study_session_creation():
    session = StudySession(player_id="player123", domain="tactics")
    assert session.id is not None
    assert session.player_id == "player123"
    assert session.domain == "tactics"
    assert session.started_at is not None
    assert session.completed_at is None

    session.completed_at = datetime.now(UTC)
    assert session.completed_at is not None


def test_content_attempt_creation():
    item_id = "test_item_id"
    attempt = ContentAttempt(
        item_id=item_id,
        player_id="player123",
        success=True,
        hint_assisted=False,
        response_payload={"uci": "e2e4"},
    )

    assert attempt.id is not None
    assert attempt.item_id == item_id
    assert attempt.player_id == "player123"
    assert attempt.success is True
    assert attempt.hint_assisted is False
    assert attempt.response_payload["uci"] == "e2e4"
    assert attempt.started_at is not None
    assert attempt.completed_at is None
