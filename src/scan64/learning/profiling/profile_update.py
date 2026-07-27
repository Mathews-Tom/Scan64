from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from scan64.content.models import ContentAttempt, ContentItem
from scan64.learning.profiling.models import ProfileObservation, SkillState
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
            success=attempt.success, hint_assisted=attempt.hint_assisted, timestamp=timestamp
        )
        updated_skills.append(skill)
    return updated_skills


def apply_analysis_observation(
    session: Session,
    player_id: str,
    game_id: str,
    position_id: str,
    skill_id: str,
    rating: int,
    observed_at: datetime,
) -> bool:
    """Apply one failed diagnosis observation exactly once."""
    key = (player_id, game_id, position_id, skill_id)
    if session.get(ProfileObservation, key) is not None:
        return False
    skill = session.get(SkillState, (player_id, skill_id))
    if skill is None:
        prior_alpha, prior_beta = get_prior_for_rating(rating)
        skill = SkillState(
            player_id=player_id,
            concept_code=skill_id,
            alpha=prior_alpha,
            beta=prior_beta,
            prior_alpha=prior_alpha,
            prior_beta=prior_beta,
        )
    skill.apply_observation(success=False, timestamp=observed_at)
    session.add(skill)
    session.add(
        ProfileObservation(
            player_id=player_id,
            game_id=game_id,
            position_id=position_id,
            skill_id=skill_id,
            observed_at=observed_at,
        )
    )
    return True


def apply_lesson_attempt(
    session: Session,
    player_id: str,
    skill_id: str,
    success: bool,
    hint_assisted: bool,
    rating: int,
    observed_at: datetime,
) -> str:
    """Apply a verified lesson result unless its taxonomy state is retired."""
    skill = session.get(SkillState, (player_id, skill_id))
    if skill is not None and skill.retired_at is not None:
        return "skipped_retired"
    if skill is None:
        prior_alpha, prior_beta = get_prior_for_rating(rating)
        skill = SkillState(
            player_id=player_id,
            concept_code=skill_id,
            alpha=prior_alpha,
            beta=prior_beta,
            prior_alpha=prior_alpha,
            prior_beta=prior_beta,
        )
    skill.apply_observation(
        success=success,
        hint_assisted=hint_assisted,
        timestamp=observed_at,
    )
    session.add(skill)
    return "applied"
