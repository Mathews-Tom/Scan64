from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule


class MigrationRule(BaseModel):
    old_id: str = Field(..., description="The original skill ID to be migrated")
    new_id: str | None = Field(
        None, description="The new skill ID to migrate to. If None, the skill is retired."
    )
    reason: str = Field(..., description="Explanation for why the skill was migrated or retired")


class TaxonomyMigrationTable(BaseModel):
    version: str = Field(..., description="The taxonomy version this migration table applies to")
    rules: dict[str, MigrationRule] = Field(..., description="Map of old_id to MigrationRule")

    def migrate(self, skill_id: str) -> tuple[str | None, str | None]:
        if skill_id in self.rules:
            rule = self.rules[skill_id]
            return rule.new_id, rule.reason
        return skill_id, None


DEFAULT_MIGRATION_TABLE = TaxonomyMigrationTable(version="current", rules={})


def migrate_active_session(
    session_skills: list[str], migration_table: TaxonomyMigrationTable
) -> tuple[list[str], list[dict[str, str]]]:
    updated_skills: list[str] = []
    retired_skills_info: list[dict[str, str]] = []
    for skill_id in session_skills:
        new_id, reason = migration_table.migrate(skill_id)
        if new_id is None:
            retired_skills_info.append({"skill_id": skill_id, "reason": reason or "Unknown"})
        elif new_id not in updated_skills:
            updated_skills.append(new_id)
    return updated_skills, retired_skills_info


def _retire_skill(skill: SkillState, reason: str, now: datetime) -> None:
    skill.retired_at = now
    skill.retirement_reason = reason


def migrate_live_rows(session: Session, migration_table: TaxonomyMigrationTable) -> None:
    """Apply explicit taxonomy rules to profile and review rows exactly once."""
    if not migration_table.rules:
        return
    now = datetime.now(UTC)
    skills = session.exec(select(SkillState).where(col(SkillState.retired_at).is_(None))).all()
    for skill in skills:
        new_id, reason = migration_table.migrate(skill.concept_code)
        if new_id == skill.concept_code:
            continue
        if new_id is None:
            _retire_skill(skill, reason or "Unknown", now)
            session.add(skill)
            continue
        target = session.get(SkillState, (skill.player_id, new_id))
        if target is None:
            target = SkillState(
                player_id=skill.player_id,
                concept_code=new_id,
                alpha=skill.alpha,
                beta=skill.beta,
                prior_alpha=skill.prior_alpha,
                prior_beta=skill.prior_beta,
                last_updated=skill.last_updated,
            )
        else:
            target.alpha += max(0.0, skill.alpha - skill.prior_alpha)
            target.beta += max(0.0, skill.beta - skill.prior_beta)
            if skill.last_updated and (
                target.last_updated is None or skill.last_updated > target.last_updated
            ):
                target.last_updated = skill.last_updated
        _retire_skill(skill, reason or f"Renamed to {new_id}", now)
        session.add(target)
        session.add(skill)

    schedules = session.exec(
        select(ReviewSchedule).where(col(ReviewSchedule.retired_at).is_(None))
    ).all()
    for schedule in schedules:
        if schedule.skill_id is None:
            continue
        new_id, reason = migration_table.migrate(schedule.skill_id)
        if new_id == schedule.skill_id:
            continue
        if new_id is None:
            schedule.retired_at = now
            schedule.retirement_reason = reason or "Unknown"
        else:
            schedule.skill_id = new_id
        session.add(schedule)
    observations = session.exec(
        select(ProfileObservation).where(col(ProfileObservation.retired_at).is_(None))
    ).all()
    for observation in observations:
        new_id, reason = migration_table.migrate(observation.skill_id)
        if new_id == observation.skill_id:
            continue
        if new_id is None:
            observation.retired_at = now
            observation.retirement_reason = reason or "Unknown"
        else:
            target_observation = session.get(
                ProfileObservation,
                (
                    observation.player_id,
                    observation.game_id,
                    observation.position_id,
                    new_id,
                ),
            )
            if target_observation is None:
                observation.skill_id = new_id
            else:
                observation.retired_at = now
                observation.retirement_reason = f"Renamed to {new_id}"
        session.add(observation)
    session.commit()
