from __future__ import annotations

import os
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient


@pytest.mark.real_model
def test_configured_maia_session_returns_a_legal_move(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    maia_binary = os.environ.get("SCAN64_MAIA_BINARY")
    maia_weights = os.environ.get("SCAN64_MAIA_1500_WEIGHTS")
    if maia_binary is None or maia_weights is None:
        pytest.fail("SCAN64_MAIA_BINARY and SCAN64_MAIA_1500_WEIGHTS are required")
    config_path = tmp_path / "maia.toml"
    config_path.write_text(
        f'''[maia]
binary_path = "{maia_binary}"
threads = 1

[maia.checkpoints]
1500 = "{maia_weights}"
'''
    )
    monkeypatch.setenv("SCAN64_MAIA_CONFIG", str(config_path))
    create_response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "maia-player",
            "opponent_config": {"provider": "maia", "strength": "1500"},
        },
    )
    assert create_response.status_code == 200

    move_response = client.post(
        f"/v1/play-sessions/{create_response.json()['id']}/moves",
        json={"move": "e2e4"},
    )

    assert move_response.status_code == 200
    opponent_move = chess.Move.from_uci(move_response.json()["opponent_move"])
    board = chess.Board()
    board.push_uci("e2e4")
    assert opponent_move in board.legal_moves

    session_response = client.get(f"/v1/play-sessions/{create_response.json()['id']}")
    assert session_response.status_code == 200
    assert session_response.json()["opponent_config"]["maia_checkpoint"] == "1500"
