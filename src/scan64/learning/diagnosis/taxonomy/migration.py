from pydantic import BaseModel, Field


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
        """
        Migrates a skill_id according to the table.
        Returns a tuple of (new_skill_id, reason).
        If the skill is not in the migration table, it returns (skill_id, None).
        If the skill is retired, it returns (None, reason).
        """
        if skill_id in self.rules:
            rule = self.rules[skill_id]
            return rule.new_id, rule.reason
        return skill_id, None


def migrate_active_session(
    session_skills: list[str], migration_table: TaxonomyMigrationTable
) -> tuple[list[str], list[dict[str, str]]]:
    """
    Migrates skills in a live ReviewSchedule or TrainingSession.
    Returns (updated_skills, retired_skills_info)
    """
    updated_skills = []
    retired_skills_info = []

    for skill_id in session_skills:
        new_id, reason = migration_table.migrate(skill_id)
        if new_id is not None:
            if new_id not in updated_skills:
                updated_skills.append(new_id)
        else:
            retired_skills_info.append({"skill_id": skill_id, "reason": reason or "Unknown"})

    return updated_skills, retired_skills_info
