from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from scan64.learning.exercises.transfer import (
    TransferPosition,
    TransferRetrievalError,
    retrieve_positions_by_motif_and_difficulty,
)


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session


def make_position(
    skill_id: str,
    difficulty: float,
    *,
    opening: str = "Sicilian Defence",
    board_side: str = "kingside",
    attacking_piece: str = "bishop",
    material_count: int = 12,
    move_number: int = 18,
) -> TransferPosition:
    return TransferPosition(
        skill_id=skill_id,
        difficulty=difficulty,
        fen="r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
        opening=opening,
        board_side=board_side,
        attacking_piece=attacking_piece,
        material_count=material_count,
        move_number=move_number,
    )


def test_retrieval_selects_matching_motif_and_difficulty(session: Session) -> None:
    closest = make_position("tactics.pin", 1500)
    second_closest = make_position("tactics.pin", 1650)
    wrong_difficulty = make_position("tactics.pin", 1800)
    wrong_motif = make_position("tactics.fork", 1500)
    session.add_all([closest, second_closest, wrong_difficulty, wrong_motif])
    session.commit()

    positions = retrieve_positions_by_motif_and_difficulty(
        session,
        skill_id="tactics.pin",
        target_difficulty=1550,
        difficulty_tolerance=150,
    )

    assert [position.id for position in positions] == [closest.id, second_closest.id]


def test_retrieval_respects_result_limit(session: Session) -> None:
    positions = [
        make_position("tactics.pin", difficulty)
        for difficulty in (1400, 1450, 1650, 1700)
    ]
    session.add_all(positions)
    session.commit()

    result = retrieve_positions_by_motif_and_difficulty(
        session,
        skill_id="tactics.pin",
        target_difficulty=1550,
        difficulty_tolerance=150,
        limit=2,
    )

    assert [position.difficulty for position in result] == [1450, 1650]


@pytest.mark.parametrize(
    ("skill_id", "target_difficulty", "difficulty_tolerance", "limit", "message"),
    [
        ("", 1500, 100, 1, "skill_id"),
        ("tactics.pin", float("nan"), 100, 1, "target_difficulty"),
        ("tactics.pin", 1500, -1, 1, "difficulty_tolerance"),
        ("tactics.pin", 1500, 100, 0, "limit"),
    ],
)
def test_retrieval_rejects_invalid_requests(
    session: Session,
    skill_id: str,
    target_difficulty: float,
    difficulty_tolerance: float,
    limit: int,
    message: str,
) -> None:
    with pytest.raises(TransferRetrievalError, match=message):
        retrieve_positions_by_motif_and_difficulty(
            session,
            skill_id=skill_id,
            target_difficulty=target_difficulty,
            difficulty_tolerance=difficulty_tolerance,
            limit=limit,
        )
