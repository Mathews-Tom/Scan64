from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from scan64.learning.exercises.transfer import TransferPosition


@dataclass(frozen=True)
class TransferPositionDefinition:
    id: str
    skill_id: str
    difficulty: float
    fen: str
    solution_uci: str
    opening: str
    board_side: str
    attacking_piece: str
    material_count: int
    move_number: int

    def to_transfer_position(self) -> TransferPosition:
        return TransferPosition(
            id=self.id,
            skill_id=self.skill_id,
            difficulty=self.difficulty,
            fen=self.fen,
            solution_uci=self.solution_uci,
            opening=self.opening,
            board_side=self.board_side,
            attacking_piece=self.attacking_piece,
            material_count=self.material_count,
            move_number=self.move_number,
        )


TRANSFER_POSITION_CATALOG: tuple[TransferPositionDefinition, ...] = (
    TransferPositionDefinition(
        id="transfer-knight-fork-001",
        skill_id="tactics.fork.knight",
        difficulty=1200.0,
        fen="k5r1/8/8/7r/4N3/8/8/1K6 w - - 0 1",
        solution_uci="b1c2",
        opening="Curated knight-fork transfer",
        board_side="queenside",
        attacking_piece="knight",
        material_count=5,
        move_number=1,
    ),
    TransferPositionDefinition(
        id="transfer-knight-fork-002",
        skill_id="tactics.fork.knight",
        difficulty=1300.0,
        fen="k5r1/8/8/7r/4N3/8/6P1/1K6 w - - 0 1",
        solution_uci="b1c2",
        opening="Curated knight-fork transfer",
        board_side="kingside",
        attacking_piece="knight",
        material_count=6,
        move_number=1,
    ),
    TransferPositionDefinition(
        id="transfer-knight-fork-003",
        skill_id="tactics.fork.knight",
        difficulty=1400.0,
        fen="k5r1/8/8/7r/4N3/8/P7/1K6 w - - 0 1",
        solution_uci="b1c2",
        opening="Curated knight-fork transfer",
        board_side="queenside",
        attacking_piece="knight",
        material_count=6,
        move_number=1,
    ),
    TransferPositionDefinition(
        id="transfer-pin-001",
        skill_id="tactics.pin",
        difficulty=1200.0,
        fen="4k3/8/2n5/1B6/8/8/P6P/7K w - - 0 1",
        solution_uci="b5c6",
        opening="Curated pin transfer",
        board_side="queenside",
        attacking_piece="bishop",
        material_count=6,
        move_number=1,
    ),
    TransferPositionDefinition(
        id="transfer-pin-002",
        skill_id="tactics.pin",
        difficulty=1300.0,
        fen="4k3/8/2n5/1B6/8/8/P7/7K w - - 0 1",
        solution_uci="b5c6",
        opening="Curated pin transfer",
        board_side="kingside",
        attacking_piece="bishop",
        material_count=5,
        move_number=1,
    ),
    TransferPositionDefinition(
        id="transfer-pin-003",
        skill_id="tactics.pin",
        difficulty=1400.0,
        fen="4k3/8/2n5/1B6/8/8/PP6/7K w - - 0 1",
        solution_uci="b5c6",
        opening="Curated pin transfer",
        board_side="queenside",
        attacking_piece="bishop",
        material_count=6,
        move_number=1,
    ),
)


def seed_transfer_positions(session: Session) -> None:
    for definition in TRANSFER_POSITION_CATALOG:
        seeded = definition.to_transfer_position()
        existing = session.get(TransferPosition, seeded.id)
        if existing is None:
            session.add(seeded)
            continue

        existing.skill_id = seeded.skill_id
        existing.difficulty = seeded.difficulty
        existing.fen = seeded.fen
        existing.solution_uci = seeded.solution_uci
        existing.opening = seeded.opening
        existing.board_side = seeded.board_side
        existing.attacking_piece = seeded.attacking_piece
        existing.material_count = seeded.material_count
        existing.move_number = seeded.move_number
        session.add(existing)
    session.commit()
