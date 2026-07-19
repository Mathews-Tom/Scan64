from __future__ import annotations

from datetime import UTC, datetime, timedelta

import chess
import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlmodel import Session, SQLModel, create_engine

from scan64.api.learning import (
    _recent_opening_family_ids,
    get_training_session,
    make_opening_spec,
)
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position  # noqa: F401
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


def test_training_session_includes_contrasting_family_after_homogeneous_history() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    player_id = "player-rotation"
    italian = OpeningFamilyPayload.model_validate(OPENING_FAMILIES[0].payload)
    now = datetime.now(UTC)

    with Session(engine) as db:
        for index in range(5):
            game = Game(
                pgn="",
                moves=_uci_moves(italian),
                white="Player",
                black="Opponent",
                created_at=now - timedelta(minutes=index),
            )
            db.add(game)
            db.flush()
            db.add(PlaySession(player_id=player_id, game_id=game.id))
        db.commit()

        scheduled_session = get_training_session(player_id=player_id, db=db)

    opening_payload_by_lesson_id = {
        make_opening_spec(item).lesson_id: OpeningFamilyPayload.model_validate(item.payload)
        for item in OPENING_FAMILIES
    }
    scheduled_openings = [
        opening_payload_by_lesson_id[lesson.lesson_id]
        for lesson in scheduled_session
        if lesson.lesson_id in opening_payload_by_lesson_id
    ]

    assert scheduled_openings
    assert any(opening.family_id == "caro_kann" for opening in scheduled_openings)



def test_recent_opening_history_is_chronological() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    opening_families = [
        OpeningFamilyPayload.model_validate(item.payload) for item in OPENING_FAMILIES
    ]
    italian = opening_families[0]
    caro_kann = opening_families[2]
    player_id = "player-history"
    now = datetime.now(UTC)

    with Session(engine) as db:
        older_game = Game(
            pgn="",
            moves=_uci_moves(italian),
            created_at=now - timedelta(minutes=1),
        )
        newer_game = Game(
            pgn="",
            moves=_uci_moves(caro_kann),
            created_at=now,
        )
        db.add(older_game)
        db.add(newer_game)
        db.flush()
        db.add(PlaySession(player_id=player_id, game_id=older_game.id))
        db.add(PlaySession(player_id=player_id, game_id=newer_game.id))
        db.commit()

        recent_family_ids = _recent_opening_family_ids(
            player_id,
            db,
            opening_families,
            history_window=5,
        )

    assert recent_family_ids == ["italian", "caro_kann"]


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
def _uci_moves(family: OpeningFamilyPayload) -> list[str]:
    board = chess.Board()
    moves: list[str] = []
    for san_move in family.moves:
        move = board.parse_san(san_move)
        moves.append(move.uci())
        board.push(move)
    return moves
