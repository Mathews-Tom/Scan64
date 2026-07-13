from scan64.content.openings.models import (
    OpeningFamilyPayload,
    OpeningMission,
    create_opening_family_item,
)


def test_opening_family_item_creation():
    payload = OpeningFamilyPayload(
        name="Italian Game",
        instructional_purpose="Rapid development, central tension, king safety.",
        moves=["e4", "e5", "Nf3", "Nc6", "Bc4"],
        missions=[
            OpeningMission(
                id="dev_minor",
                description="Develop both minor pieces before starting an attack.",
                invariant_type="minor_pieces_developed",
            )
        ],
    )

    item = create_opening_family_item(
        provenance="Test Author",
        licence="CC0-1.0",
        payload=payload,
        difficulty_estimate=1200.0,
        skill_mapping={"openings.italian": 1.0},
    )

    assert item.domain == "openings"
    assert item.provenance == "Test Author"
    assert item.licence == "CC0-1.0"
    assert item.difficulty_estimate == 1200.0

    parsed_payload = OpeningFamilyPayload.model_validate(item.payload)
    assert parsed_payload.name == "Italian Game"
    assert parsed_payload.missions[0].id == "dev_minor"


def test_family_count():
    from scan64.content.openings.curated import OPENING_FAMILIES

    # Acceptance: exactly 3 opening families are shipped
    assert len(OPENING_FAMILIES) == 3
