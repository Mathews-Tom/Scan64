from __future__ import annotations

import pytest

from scan64.content.openings.curated import OPENING_FAMILIES
from scan64.content.openings.models import OpeningFamilyPayload
from scan64.learning.scheduling.opening_rotation import (
    OpeningRotationPlanner,
    classify_opening_family,
)


@pytest.fixture(name="families")
def families_fixture() -> list[OpeningFamilyPayload]:
    return [OpeningFamilyPayload.model_validate(item.payload) for item in OPENING_FAMILIES]


def test_rotation_logic_requires_opposite_colour_contrast_after_homogeneous_history(
    families: list[OpeningFamilyPayload],
) -> None:
    plan = OpeningRotationPlanner(history_window=3).plan(
        families,
        recent_family_ids=["italian", "italian", "italian"],
    )

    assert plan.required_family_id == "caro_kann"
    assert plan.ordered_family_ids[:2] == ("caro_kann", "italian")
    assert plan.familiar_family_id == "italian"
    assert plan.response_review_family_id == "italian"


def test_rotation_logic_waits_for_complete_history_window(
    families: list[OpeningFamilyPayload],
) -> None:
    plan = OpeningRotationPlanner(history_window=3).plan(
        families,
        recent_family_ids=["queens_gambit", "queens_gambit"],
    )

    assert plan.required_family_id is None
    assert plan.familiar_family_id == "queens_gambit"
    assert plan.response_review_family_id == "queens_gambit"


def test_rotation_logic_classifies_curated_uci_prefix(
    families: list[OpeningFamilyPayload],
) -> None:
    family_id = classify_opening_family(
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6"],
        families,
    )

    assert family_id == "italian"


def test_rotation_logic_rejects_unknown_history_family(
    families: list[OpeningFamilyPayload],
) -> None:
    with pytest.raises(ValueError, match="Unknown opening family IDs"):
        OpeningRotationPlanner().plan(families, recent_family_ids=["english"])
