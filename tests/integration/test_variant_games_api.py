"""PGN import reads the Variant tag; variant games stay out of standard analysis."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.games.models import Game

CRAZYHOUSE = '[Variant "Crazyhouse"]\n[Result "*"]\n\n1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 4. P@e5 *\n'


def _register(db_session: Session, player_id: str) -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def _import(client: TestClient, headers: dict[str, str], pgn: str) -> object:
    return client.post(
        "/v1/games",
        json={"owner_color": "white", "pgn": pgn, "player_id": "variant-player"},
        headers=headers,
    )


def test_crazyhouse_game_imports_with_its_variant_and_is_not_analysed(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session, "variant-player")
    response = _import(client, headers, CRAZYHOUSE)
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["variant"] == "crazyhouse"
    game = db_session.get(Game, UUID(body["id"]))
    assert game is not None
    assert game.moves[-1] == "P@e5"

    listed = client.get("/v1/games", headers=headers).json()["items"]
    assert [(item["id"], item["variant"]) for item in listed] == [(body["id"], "crazyhouse")]

    job = client.post(f"/v1/games/{body['id']}/analysis-jobs", headers=headers)
    assert job.status_code == 409
    assert job.json()["detail"] == {
        "code": "variant_not_covered",
        "variant": "crazyhouse",
        "message": "Crazyhouse games are not covered by game analysis yet; "
        "Scan64 covers standard chess there.",
    }
    # A drop is illegal in standard chess: reading it as standard was a 500.
    for path in ("review", "review/backfill"):
        refused = client.request(
            "GET" if path == "review" else "POST", f"/v1/games/{body['id']}/{path}", headers=headers
        )
        assert refused.status_code == 409, refused.json()
        assert refused.json()["detail"]["message"].startswith(
            "Crazyhouse games are not covered by game review"
        )


def test_unsupported_variant_and_illegal_variant_moves_are_refused(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session, "variant-player")
    chess960 = _import(client, headers, '[Variant "Chess960"]\n\n1. e4 e5 *\n')
    assert chess960.status_code == 422
    assert "Variant 'Chess960' is not supported" in chess960.json()["detail"]
    illegal = _import(client, headers, '[Variant "Crazyhouse"]\n\n1. e4 d5 2. N@e5 *\n')
    assert illegal.status_code == 400
    assert illegal.json()["detail"].startswith("Invalid PGN:")
    standard = _import(client, headers, "1. e4 e5 *\n")
    assert standard.status_code == 200 and standard.json()["variant"] == "chess"


def test_legacy_game_with_an_unsupported_tag_lists_and_is_not_analysed(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session, "variant-player")
    legacy = Game(
        pgn="1. e4 *",
        headers={"Variant": "Bughouse"},
        owner_player_id="variant-player",
        owner_color="white",
    )
    db_session.add(legacy)
    db_session.commit()
    listed = client.get("/v1/games", headers=headers).json()["items"]
    assert [item["variant"] for item in listed] == ["Bughouse"]
    job = client.post(f"/v1/games/{legacy.id}/analysis-jobs", headers=headers)
    assert job.status_code == 409
    assert job.json()["detail"]["message"].startswith("Bughouse games are not covered")


def _position(client: TestClient, headers: dict[str, str], **body: object) -> object:
    return client.post("/v1/variants/position", json=body, headers=headers)


def test_variant_position_reports_drops_pockets_and_san(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session, "variant-player")
    moves = ["e2e4", "d7d5", "e4d5", "d8d5", "b1c3", "d5a5"]
    response = _position(client, headers, variant="crazyhouse", moves=moves)
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["pockets"] == {"white": {"p": 1}, "black": {"p": 1}}
    assert "P@e5" in body["legal_moves"]
    assert body["san"][-1] == "Qa5"
    dropped = _position(client, headers, variant="crazyhouse", moves=[*moves, "P@e5"]).json()
    assert dropped["san"][-1] == "@e5"
    assert dropped["pockets"] == {"white": {}, "black": {"p": 1}}
    assert body["remaining_checks"] is None


def test_variant_position_counts_three_check_and_ends_the_game(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session, "variant-player")
    first = _position(
        client, headers, variant="3check", moves=["e2e4", "e7e5", "f1c4", "b8c6", "c4f7"]
    )
    body = first.json()
    assert body["check"] is True
    assert body["remaining_checks"] == {"white": 2, "black": 3}
    assert body["outcome"] is None
    horde = _position(client, headers, variant="horde").json()
    assert (
        horde["fen"] == "rnbqkbnr/pppppppp/8/1PP2PP1/PPPPPPPP/PPPPPPPP/PPPPPPPP/PPPPPPPP w kq - 0 1"
    )
    king = _position(
        client,
        headers,
        variant="kingofthehill",
        # e4 is a centre square: White's king reaching it wins at once.
        moves=["d2d3", "e7e6", "e1d2", "e8e7", "d2e3", "e7d6", "e3e4"],
    ).json()
    # python-chess names the termination from the side to move: Black has lost.
    assert king["outcome"] == {"result": "1-0", "termination": "variant_loss"}


def test_variant_position_refuses_illegal_moves_and_unknown_variants(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session, "variant-player")
    illegal = _position(client, headers, variant="crazyhouse", moves=["e2e4", "N@e5"])
    assert illegal.status_code == 422
    assert illegal.json()["detail"].startswith("Move 2 N@e5 is illegal")
    unknown = _position(client, headers, variant="bughouse")
    assert unknown.status_code == 422
    anonymous = client.post("/v1/variants/position", json={"variant": "atomic"})
    assert anonymous.status_code == 401
