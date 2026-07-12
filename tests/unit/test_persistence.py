import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from scan64.chess.games.ingestion import ingest_pgn
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

def test_persist_game_and_positions(session: Session):
    pgn = """[Event "Test"]
[White "Player1"]
[Black "Player2"]
1. e4 e5 2. Nf3 Nc6
"""
    game, positions = ingest_pgn(pgn)

    session.add(game)
    for pos in positions:
        session.add(pos)

    session.commit()

    # Retrieve game
    saved_game = session.get(Game, game.id)
    assert saved_game is not None
    assert saved_game.white == "Player1"
    assert saved_game.black == "Player2"
    assert len(saved_game.moves) == 4

    # Retrieve positions
    saved_positions = session.exec(select(Position).where(Position.game_id == game.id)).all()
    assert len(saved_positions) == 5 # initial pos + 4 moves
