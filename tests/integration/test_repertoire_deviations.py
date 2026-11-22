from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.chess.games.models import Game, PlaySession
from scan64.learning.repertoire import RepertoireAttempt
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from tests.integration.test_data_lifecycle_completeness import _authorization, _create_player_token

OWNER = "deviations-owner"
OTHER = "deviations-other"


def _import_game(
    client: TestClient, token: dict[str, str], owner: str, pgn: str, *, color: str = "white"
) -> UUID:
    response = client.post(
        "/v1/games", headers=token, json={"player_id": owner, "pgn": pgn, "owner_color": color}
    )
    assert response.status_code == 200, response.text
    return UUID(response.json()["id"])


def _import_repertoire(client: TestClient, token: dict[str, str], pgn: str) -> str:
    response = client.post("/v1/repertoires", headers=token, json={"pgn": pgn})
    assert response.status_code == 201, response.text
    repertoire_id: str = response.json()[0]["id"]
    return f"/v1/repertoires/{repertoire_id}"


def test_ranks_first_departures_and_links_to_exact_verified_drill(
    client: TestClient, db_session: Session
) -> None:
    owner = _authorization(_create_player_token(client, OWNER))
    other = _authorization(_create_player_token(client, OTHER))
    url = _import_repertoire(client, owner, "1. d4 d5 2. Nf3 Nf6 3. e3 e6 *")
    # c5 in the same position reached by two move orders, and with both owner colours.
    _import_game(client, owner, OWNER, "1. Nf3 d5 2. d4 c5 3. e3 *")
    _import_game(client, owner, OWNER, "1. d4 d5 2. Nf3 c5 *")
    _import_game(client, owner, OWNER, "1. d4 d5 2. Nf3 c5 *", color="black")
    # Both continuations follow the repertoire. c4 is beyond its last decision.
    _import_game(client, owner, OWNER, "1. Nf3 d5 2. d4 Nf6 3. e3 e6 *")
    _import_game(client, owner, OWNER, "1. d4 d5 2. Nf3 Nf6 3. e3 e6 4. c4 *")
    _import_game(client, owner, OWNER, "1. d4 d5 2. Nf3 *")
    _import_game(client, owner, OWNER, "*")
    linked = _import_game(client, owner, OWNER, "1. e4 *")
    db_session.add(PlaySession(player_id=OWNER, game_id=linked))
    db_session.add(Game(pgn="", moves=["e2e4"]))  # Ownerless legacy/CLI import.
    db_session.commit()
    _import_game(client, other, OTHER, "1. c4 *")

    assert client.get(f"{url}/deviations").status_code == 401
    assert client.get(f"{url}/deviations", headers=other).status_code == 404
    response = client.get(f"{url}/deviations", headers=owner)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "covered_games": 6,
        "uncovered_games": 1,
        "deviations": [
            {"node_id": 3, "uci": "c7c5", "san": "c5", "played_by": "opponent", "games": 2},
            {"node_id": 3, "uci": "c7c5", "san": "c5", "played_by": "you", "games": 1},
        ],
    }
    assert db_session.exec(select(RepertoireAttempt)).first() is None
    assert db_session.exec(select(ReviewSchedule)).first() is None

    node_id = response.json()["deviations"][0]["node_id"]
    for move, success in [("c7c5", False), ("g8f6", True)]:
        attempt = client.post(
            f"{url}/attempts",
            headers=owner,
            json={"attempt_id": str(uuid4()), "submitted_move": move, "node_id": node_id},
        )
        assert attempt.status_code == 200, attempt.text
        assert attempt.json()["success"] is success


def test_repetition_does_not_erase_the_first_departure(client: TestClient) -> None:
    token = _authorization(_create_player_token(client, OWNER))
    url = _import_repertoire(client, token, "1. e4 e5 2. Nf3 Nc6 3. Bb5 *")
    for game in (
        "1. e4 e5 2. Nf3 Nc6 3. Ng1 Nb8 4. Nf3 Nc6 5. Bb5 *",
        "1. e4 e5 2. Nf3 Nc6 3. Ng1 Nf6 4. Nf3 Ng8 5. Bb5 *",
    ):
        _import_game(client, token, OWNER, game)
    response = client.get(f"{url}/deviations", headers=token)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "covered_games": 2,
        "uncovered_games": 0,
        "deviations": [{"node_id": 4, "uci": "f3g1", "san": "Ng1", "played_by": "you", "games": 2}],
    }


@pytest.mark.parametrize(
    ("base_fen", "saved", "different_fen", "move"),
    [
        ("4k3/8/8/3pP3/8/8/8/4K3 w - d6", "2. exd6", "4k3/8/8/3pP3/8/8/8/4K3 w - -", "2. e6"),
        ("4k3/8/8/8/8/8/8/4K2R w K -", "2. O-O", "4k3/8/8/8/8/8/8/4K2R w - -", "2. Kd2"),
        ("4k3/8/8/8/8/8/8/4K2R w - -", "2. Rh2", "4k3/8/8/8/8/8/8/4K2R b - -", "2... Kd7"),
    ],
)
def test_distinguishes_turn_castling_and_legal_en_passant_but_ignores_clocks(
    client: TestClient, base_fen: str, saved: str, different_fen: str, move: str
) -> None:
    token = _authorization(_create_player_token(client, OWNER))
    pgn = f'[SetUp "1"]\n[FEN "{base_fen} 0 2"]\n\n{saved} *'
    url = _import_repertoire(client, token, pgn)
    _import_game(client, token, OWNER, f'[SetUp "1"]\n[FEN "{different_fen} 0 2"]\n\n{move} *')
    setup = f'[Variant "From Position"]\n[SetUp "1"]\n[FEN "{base_fen} 19 40"]'
    _import_game(client, token, OWNER, f"{setup}\n\n40. {saved.split()[1]} *")
    response = client.get(f"{url}/deviations", headers=token)
    assert response.status_code == 200, response.text
    assert response.json() == {"covered_games": 1, "uncovered_games": 1, "deviations": []}
