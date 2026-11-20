"""Invalid restored starting FENs remain controlled, owned source conflicts."""

from __future__ import annotations

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.games.assistance import SessionHintReveal
from scan64.chess.games.models import Game, PlaySession


@pytest.mark.parametrize("route", ["grade", "hint"])
def test_unparseable_owned_start_is_conflict_without_disclosing_foreign_source(
    client: TestClient, db_session: Session, route: str
) -> None:
    auth: dict[str, dict[str, str]] = {}
    for actor in ("corrupt-position-owner", "corrupt-position-other"):
        token, token_hash = issue_player_token()
        db_session.add(Player(id=actor))
        db_session.add(PlayerCredential(player_id=actor, token_hash=token_hash))
        auth[actor] = {"Authorization": f"Bearer {token}"}
    game = Game(pgn="*", headers={"FEN": "bad"}, moves=[], owner_player_id="corrupt-position-owner")
    db_session.add(game)
    db_session.flush()
    play = PlaySession(
        player_id="corrupt-position-owner",
        game_id=game.id,
        opponent_config={"player_color": "white", "assistance_generation": "0"},
        assistance_tracking_version=1,
    )
    db_session.add(play)
    db_session.commit()
    play_id, game_id = play.id, game.id

    for actor, expected_status in (
        ("corrupt-position-owner", 409),
        ("corrupt-position-other", 404),
    ):
        if route == "grade":
            response = client.post(
                f"/v1/play-sessions/{play_id}/assistance",
                headers=auth[actor],
                json={
                    "player_id": actor,
                    "fen": chess.STARTING_FEN,
                    "moves": ["e2e4"],
                    "mode": "guard",
                },
            )
        else:
            response = client.post(
                "/v1/insights/position",
                headers=auth[actor],
                json={"player_id": actor, "play_session_id": str(play_id), "step": 1},
            )
        assert response.status_code == expected_status, response.text
        if expected_status == 409:
            assert response.json() == {"detail": "Stored game has illegal moves"}

    db_session.expire_all()
    retained_game = db_session.get(Game, game_id)
    retained_play = db_session.get(PlaySession, play_id)
    assert retained_game is not None and retained_game.headers == {"FEN": "bad"}
    assert retained_game.moves == []
    assert retained_play is not None
    assert retained_play.opponent_config["assistance_generation"] == "0"
    assert db_session.exec(select(SessionHintReveal)).all() == []
