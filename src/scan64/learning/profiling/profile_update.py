from datetime import UTC, datetime

from scan64.content.models import ContentAttempt, ContentItem
from scan64.learning.profiling.models import SkillState


def apply_content_attempt(
    attempt: ContentAttempt, item: ContentItem, existing_skills: list[SkillState]
) -> list[SkillState]:
    """Apply one content attempt to the player's mapped skill states."""
    skill_map = {
        skill.concept_code: skill
        for skill in existing_skills
        if skill.player_id == attempt.player_id
    }
    timestamp = attempt.completed_at or datetime.now(UTC)
    updated_skills: list[SkillState] = []

    for concept_code, weight in item.skill_mapping.items():
        if weight <= 0:
            continue

        skill = skill_map.get(concept_code)
        if skill is None:
            skill = SkillState(player_id=attempt.player_id, concept_code=concept_code)
        skill.apply_observation(
            success=attempt.success,
            hint_assisted=attempt.hint_assisted,
            timestamp=timestamp,
        )
        updated_skills.append(skill)

    return updated_skills
