import pytest

from scan64.learning.diagnosis.taxonomy.migration import (
    MigrationRule,
    TaxonomyMigrationTable,
    migrate_active_session,
)


@pytest.fixture
def migration_table() -> TaxonomyMigrationTable:
    return TaxonomyMigrationTable(
        version="1.1.0",
        rules={
            "old.skill.1": MigrationRule(
                old_id="old.skill.1", new_id="new.skill.1", reason="Renamed to new.skill.1"
            ),
            "old.skill.2": MigrationRule(
                old_id="old.skill.2", new_id=None, reason="Too generic, retiring"
            ),
            "old.skill.3": MigrationRule(
                old_id="old.skill.3", new_id="new.skill.3", reason="Consolidated"
            ),
        },
    )


def test_migrate_skill_renamed(migration_table: TaxonomyMigrationTable) -> None:
    new_id, reason = migration_table.migrate("old.skill.1")
    assert new_id == "new.skill.1"
    assert reason == "Renamed to new.skill.1"


def test_migrate_skill_retired(migration_table: TaxonomyMigrationTable) -> None:
    new_id, reason = migration_table.migrate("old.skill.2")
    assert new_id is None
    assert reason == "Too generic, retiring"


def test_migrate_skill_not_in_table(migration_table: TaxonomyMigrationTable) -> None:
    new_id, reason = migration_table.migrate("stable.skill")
    assert new_id == "stable.skill"
    assert reason is None


def test_migrate_active_session(migration_table: TaxonomyMigrationTable) -> None:
    session_skills = ["old.skill.1", "stable.skill", "old.skill.2", "old.skill.3", "old.skill.1"]
    updated_skills, retired_info = migrate_active_session(session_skills, migration_table)

    # old.skill.1 becomes new.skill.1. stable.skill is untouched. old.skill.2 is retired.
    # old.skill.3 becomes new.skill.3. The second old.skill.1 is skipped because
    # new.skill.1 is already
    # in the list.
    assert updated_skills == ["new.skill.1", "stable.skill", "new.skill.3"]
    assert retired_info == [{"skill_id": "old.skill.2", "reason": "Too generic, retiring"}]
