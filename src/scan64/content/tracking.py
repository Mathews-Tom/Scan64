from datetime import UTC, datetime

from scan64.content.models import ContentAttempt, ContentItem
from scan64.learning.profiling.models import SkillState


def apply_content_attempt_to_profile(
    attempt: ContentAttempt, item: ContentItem, existing_skills: list[SkillState]
) -> list[SkillState]:
    """
    Route a content attempt into the shared player skill model.
    Takes a ContentAttempt, its corresponding ContentItem, and a list of the player's
    current SkillState records for the relevant concepts.
    Returns the updated (or newly created) SkillState records.
    """
    updated_skills = []

    # Map of existing skills for easy access
    skill_map = {s.concept_code: s for s in existing_skills if s.player_id == attempt.player_id}

    timestamp = attempt.completed_at or datetime.now(UTC)

    for concept_code, weight in item.skill_mapping.items():
        if weight <= 0:
            continue

        if concept_code in skill_map:
            skill = skill_map[concept_code]
        else:
            skill = SkillState(
                player_id=attempt.player_id,
                concept_code=concept_code,
                # Start with prior
            )

        # Apply the observation using the shared learning model logic.
        # If the item maps to this skill with a certain weight, we might want
        # to scale the alpha/beta updates in a fuller implementation, but for now
        # the standard apply_observation works.
        skill.apply_observation(
            success=attempt.success, hint_assisted=attempt.hint_assisted, timestamp=timestamp
        )
        updated_skills.append(skill)

    return updated_skills
