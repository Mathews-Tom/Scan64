import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from scan64.api.app import app
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.persistence.database import get_session


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    from scan64.persistence import database
    database.engine = engine
    # Need all models imported for create_all to work
    from scan64.api.middleware import IdempotencyRecord  # noqa: F401
    from scan64.api.models import Player, PlayerProfile  # noqa: F401
    from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis  # noqa: F401
    from scan64.chess.games.models import Game, PlaySession  # noqa: F401
    from scan64.chess.positions.models import Position  # noqa: F401
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s

@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session
    app.dependency_overrides[get_session] = get_session_override
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_play_from_here_api_flow(client: TestClient, session: Session):
    # 1. Create a game with a specific FEN
    fen = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2"
    pgn = f'[FEN "{fen}"]\n[SetUp "1"]\n\n'
    response = client.post("/v1/games", json={"pgn": pgn})
    assert response.status_code == 200
    game_id = response.json()["id"]

    # 2. Create a PlaySession from this game
    response = client.post(
        "/v1/play-sessions",
        json={"player_id": "test_user", "game_id": game_id}
    )
    assert response.status_code == 200
    session_id = response.json()["id"]

    # 3. Make a move and ensure the board starts from the FEN
    # White to move (w), so player plays e1e2 for example (a bad move, but legal!)
    # Actually let's play g1f3 (Nf3)
    response = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "g1f3"}
    )
    assert response.status_code == 200

    import uuid
    game = session.get(Game, uuid.UUID(game_id))
    assert len(game.moves) == 2 # 1 player move, 1 opponent move



def test_game_positions_are_returned_in_ply_order(client: TestClient, session: Session):
    game = Game(pgn="")
    session.add(game)
    session.flush()
    session.add_all(
        [
            Position(
                game_id=game.id,
                fen="8/8/8/8/8/8/8/K6k b - - 0 2",
                half_move_clock=0,
                full_move_number=2,
                side_to_move="b",
                canonical_id="late",
            ),
            Position(
                game_id=game.id,
                fen="8/8/8/8/8/8/8/K6k b - - 0 1",
                half_move_clock=0,
                full_move_number=1,
                side_to_move="b",
                canonical_id="after-white",
            ),
            Position(
                game_id=game.id,
                fen="8/8/8/8/8/8/8/K6k w - - 0 1",
                half_move_clock=0,
                full_move_number=1,
                side_to_move="w",
                canonical_id="initial",
            ),
        ]
    )
    session.commit()

    response = client.get(f"/v1/games/{game.id}/positions")

    assert response.status_code == 200
    assert [
        (position["full_move_number"], position["side_to_move"])
        for position in response.json()
    ] == [(1, "w"), (1, "b"), (2, "b")]