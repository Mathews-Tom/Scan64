from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scan64.content.openings.curated import OPENING_FAMILIES
from scan64.content.openings.models import OpeningFamilyPayload
from scan64.learning.scheduling.composer import SessionComposer
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


@given(st.sampled_from(("italian", "queens_gambit", "caro_kann")))
def test_rotation_property_schedules_contrast_within_one_session(
    familiar_family_id: str,
) -> None:
    opening_families = [
        OpeningFamilyPayload.model_validate(item.payload) for item in OPENING_FAMILIES
    ]
    family_by_id = {family.family_id: family for family in opening_families}
    rotation_plan = OpeningRotationPlanner(history_window=5).plan(
        opening_families,
        recent_family_ids=[familiar_family_id] * 5,
    )
    assert rotation_plan.required_family_id is not None

    pool = [
        {
            "id": family.family_id,
            "type": "exploration",
            "priority": 0.0,
        }
        for family in opening_families
    ]

    session = SessionComposer().compose_session(
        pool,
        session_size=1,
        required_item_ids=(rotation_plan.required_family_id,),
    )

    assert len(session) == 1
    assert (
        family_by_id[session[0]["id"]].structure
        != family_by_id[familiar_family_id].structure
    )



def test_session_composer_limits_required_items_to_session_size() -> None:
    pool = [
        {"id": "first", "type": "mistakes", "priority": 1.0},
        {"id": "second", "type": "mistakes", "priority": 1.0},
        {"id": "due", "type": "due", "priority": 1.0},
        {"id": "exploration", "type": "exploration", "priority": 1.0},
    ]

    session = SessionComposer(hard_exploration_floor=0.5).compose_session(
        pool,
        session_size=2,
        required_item_ids=("first", "second"),
    )

    assert [item["id"] for item in session] == ["first", "second"]
