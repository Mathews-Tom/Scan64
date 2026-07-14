from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from scan64.api.app import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c

def test_get_daily_training_session(client: TestClient) -> None:
    response = client.get("/v1/learning/session?player_id=test_player")
    assert response.status_code == 200

    data = response.json()
    assert len(data) == 5 # Session size is 5 in our API

    lesson_ids = [item["lesson_id"] for item in data]

    # Check that we have a mix of content types
    assert any("opening" in lid for lid in lesson_ids)
    assert any("endgame" in lid for lid in lesson_ids)

    # Verify actual sources behavior
    for spec in data:
        if "opening" in spec["lesson_id"]:
            # Penultimate move means fen is NOT starting fen
            assert spec["source"]["fen"] != (
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            )
        elif spec["diagnosis"]["primary"] == "endgame":
            # Accepted moves must be SAN, not UCI format (e.g. e2e4 or f7f8q)
            for move in spec["interaction"]["accepted_moves"]:
                # Rough check that it isn't raw UCI
                import re
                assert not re.match(r"^[a-h][1-8][a-h][1-8][qrbn]?$", move["san"]), (
                    f"Raw UCI detected: {move['san']}"
                )
