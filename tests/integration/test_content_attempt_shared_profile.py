from datetime import UTC, datetime

from scan64.content import ContentAttempt, ContentItem, apply_content_attempt_to_profile
from scan64.learning.profiling.models import SkillState


def test_content_attempt_updates_skill_state():
    # 1. Setup a ContentItem representing a tactic
    item = ContentItem(
        domain="tactics",
        provenance="Test",
        licence="CC0",
        skill_mapping={"tactics.fork": 1.0, "tactics.pin": 0.5},
    )

    # 2. Setup existing SkillState for the player
    player_id = "player_123"
    existing_skill = SkillState(
        player_id=player_id, concept_code="tactics.fork", alpha=2.0, beta=1.0
    )

    # 3. Create a ContentAttempt
    attempt = ContentAttempt(
        item_id=item.id,
        player_id=player_id,
        success=True,
        hint_assisted=False,
        completed_at=datetime.now(UTC),
    )

    # 4. Apply attempt to profile
    updated_skills = apply_content_attempt_to_profile(attempt, item, [existing_skill])

    # 5. Verify the skill was updated (success=True increases alpha by 1.0)
    assert len(updated_skills) == 2

    fork_skill = next(s for s in updated_skills if s.concept_code == "tactics.fork")
    pin_skill = next(s for s in updated_skills if s.concept_code == "tactics.pin")

    assert fork_skill.player_id == player_id
    assert fork_skill.alpha == 3.0  # 2.0 (initial) + 1.0 (success)
    assert fork_skill.beta == 1.0

    assert pin_skill.player_id == player_id
    assert pin_skill.alpha == 2.0  # 1.0 (default) + 1.0 (success)
    assert pin_skill.beta == 1.0
