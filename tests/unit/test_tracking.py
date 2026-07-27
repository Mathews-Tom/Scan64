from datetime import UTC, datetime

from scan64.content.models import ContentAttempt, ContentItem
from scan64.content.tracking import apply_content_attempt_to_profile
from scan64.learning.profiling.models import SkillState


def test_tracking_success_updates_existing_skill_with_attempt_timestamp() -> None:
    player_id = "player-1"
    completed_at = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    attempt = ContentAttempt(
        item_id="famous-game",
        player_id=player_id,
        success=True,
        hint_assisted=False,
        completed_at=completed_at,
    )
    item = ContentItem(
        id="famous-game",
        domain="famous_games",
        provenance="test",
        licence="CC0",
        skill_mapping={"tactics.sacrifice": 1.0},
    )
    skill = SkillState(
        player_id=player_id,
        concept_code="tactics.sacrifice",
        alpha=2.0,
        beta=3.0,
    )

    updated = apply_content_attempt_to_profile(attempt, item, [skill])

    assert updated == [skill]
    assert skill.alpha == 3.0
    assert skill.beta == 3.0
    assert skill.last_updated == completed_at


def test_tracking_failure_creates_only_positive_weight_skill() -> None:
    player_id = "player-1"
    attempt = ContentAttempt(
        item_id="famous-game",
        player_id=player_id,
        success=False,
        hint_assisted=False,
        completed_at=datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
    )
    item = ContentItem(
        id="famous-game",
        domain="famous_games",
        provenance="test",
        licence="CC0",
        skill_mapping={"tactics.sacrifice": 1.0, "tactics.ignored": 0.0},
    )
    unrelated = SkillState(
        player_id="player-2",
        concept_code="tactics.sacrifice",
        alpha=4.0,
        beta=2.0,
    )

    updated = apply_content_attempt_to_profile(attempt, item, [unrelated])

    assert [(skill.player_id, skill.concept_code) for skill in updated] == [
        (player_id, "tactics.sacrifice")
    ]
    assert updated[0].alpha == 1.0
    assert updated[0].beta == 2.0
