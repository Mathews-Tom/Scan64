from datetime import UTC, datetime

from scan64.content.models import ContentAttempt, ContentItem
from scan64.learning.profiling.models import SkillState
from scan64.learning.profiling.priors import get_prior_for_rating


def apply_content_attempt(
    attempt: ContentAttempt,
    item: ContentItem,
    existing_skills: list[SkillState],
    rating: int,
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
            prior_alpha, prior_beta = get_prior_for_rating(rating)
            skill = SkillState(
                player_id=attempt.player_id,
                concept_code=concept_code,
                alpha=prior_alpha,
                beta=prior_beta,
                prior_alpha=prior_alpha,
                prior_beta=prior_beta,
            )
        skill.apply_observation(
            success=attempt.success,
            hint_assisted=attempt.hint_assisted,
            timestamp=timestamp,
        )
        updated_skills.append(skill)

    return updated_skills
