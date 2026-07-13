from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from scan64.api.app import app
from scan64.chess.analysis.jobs import execute_analysis_job


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_pgn() -> str:
    return """[Event "Test"]
[Site "Test"]
[Date "2024.01.01"]
[Round "1"]
[White "Player1"]
[Black "Player2"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 d6 4. O-O h6 5. d4 exd4 6. Nxd4 Nxd4 7. Qxd4 c5 8. Qd5
Be6 9. Bb5+ Bd7 10. Bxd7+ Qxd7 1-0"""


def test_learning_opportunities_flow(client: TestClient, sample_pgn: str) -> None:
    # 1. Create a game
    response = client.post("/v1/games", json={"pgn": sample_pgn})
    assert response.status_code == 200
    game_id = response.json()["id"]

    # 2. Trigger analysis job
    response = client.post(f"/v1/games/{game_id}/analysis-jobs")
    assert response.status_code == 200
    job_id = response.json()["id"]

    # 3. Simulate executing background task synchronously for testing
    execute_analysis_job(UUID(job_id))

    # 4. Check job status
    response = client.get(f"/v1/analysis-jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    # 5. Fetch learning opportunities
    response = client.get(f"/v1/games/{game_id}/learning-opportunities")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)
