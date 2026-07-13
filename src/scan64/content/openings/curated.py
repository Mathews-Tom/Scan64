from scan64.content.openings.models import (
    OpeningFamilyPayload,
    OpeningMission,
    create_opening_family_item,
)

OPENING_FAMILIES = [
    create_opening_family_item(
        provenance="Original Scan64 composition (2026-07-14)",
        licence="CC0-1.0",
        difficulty_estimate=1200.0,
        skill_mapping={"openings.italian": 1.0},
        payload=OpeningFamilyPayload(
            name="Italian Game",
            instructional_purpose="Rapid development, central tension, king safety.",
            moves=["e4", "e5", "Nf3", "Nc6", "Bc4"],
            missions=[
                OpeningMission(
                    id="italian_dev_minor",
                    description="Develop both minor pieces before starting an attack.",
                    invariant_type="minor_pieces_developed",
                )
            ],
        ),
    ),
    create_opening_family_item(
        provenance="Original Scan64 composition (2026-07-14)",
        licence="CC0-1.0",
        difficulty_estimate=1400.0,
        skill_mapping={"openings.queens_gambit": 1.0},
        payload=OpeningFamilyPayload(
            name="Queen's Gambit",
            instructional_purpose="Pawn tension, space, minority structures, development.",
            moves=["d4", "d5", "c4"],
            missions=[
                OpeningMission(
                    id="qg_central_tension",
                    description="Maintain central pawn tension without capturing prematurely.",
                    invariant_type="pawn_tension_maintained",
                )
            ],
        ),
    ),
    create_opening_family_item(
        provenance="Original Scan64 composition (2026-07-14)",
        licence="CC0-1.0",
        difficulty_estimate=1300.0,
        skill_mapping={"openings.caro_kann": 1.0},
        payload=OpeningFamilyPayload(
            name="Caro-Kann Defense",
            instructional_purpose="Central response styles and defensive planning.",
            moves=["e4", "c6"],
            missions=[
                OpeningMission(
                    id="ck_solid_structure",
                    description="Establish a solid pawn structure without early weaknesses.",
                    invariant_type="solid_pawn_structure",
                )
            ],
        ),
    ),
]
