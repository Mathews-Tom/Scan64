from uuid import uuid4

from fastapi.testclient import TestClient


def test_get_daily_training_session(client: TestClient) -> None:
    player_id = str(uuid4())
    created = client.post("/v1/players", json={"id": player_id})
    assert created.status_code == 200

    response = client.get(
        f"/v1/learning/session?player_id={player_id}",
        headers={"Authorization": f"Bearer {created.json()['access_token']}"},
    )
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data["session_id"], str)
    assert data["lessons"] == []
