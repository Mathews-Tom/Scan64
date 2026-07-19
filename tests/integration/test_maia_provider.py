from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.chess.games.models import PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.providers.maia import MaiaCheckpoint, MaiaConfig
from scan64.providers.stockfish.adapter import StockfishConfig


def test_play_session_rejects_unknown_opponent_provider(client: TestClient) -> None:
    response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "maia-player",
            "opponent_config": {"provider": "unknown", "strength": "1500"},
        },
    )

    assert response.status_code == 422
    assert "Unsupported opponent provider" in response.text


def test_maia_selection_fails_closed_without_operator_configuration(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.delenv("SCAN64_MAIA_CONFIG", raising=False)
    create_response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "maia-player",
            "opponent_config": {"provider": "maia", "strength": "1500"},
        },
    )
    assert create_response.status_code == 200

    session_id = create_response.json()["id"]
    move_response = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4"},
    )

    assert move_response.status_code == 500
    assert move_response.json()["detail"] == (
        "Maia is not configured. Set SCAN64_MAIA_CONFIG to an operator-provided config file."
    )

def test_malformed_maia_config_does_not_disable_stockfish_play(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("SCAN64_MAIA_CONFIG", "/operator/malformed-maia.toml")
    create_response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "stockfish-player",
            "opponent_config": {"provider": "stockfish", "strength": "0"},
        },
    )
    assert create_response.status_code == 200

    move_response = client.post(
        f"/v1/play-sessions/{create_response.json()['id']}/moves",
        json={"move": "e2e4"},
    )

    assert move_response.status_code == 200
    assert move_response.json()["opponent_move"] is not None


def test_malformed_maia_config_reports_actionable_error_for_maia_session(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("SCAN64_MAIA_CONFIG", "/operator/malformed-maia.toml")
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

    assert move_response.status_code == 500
    assert move_response.json()["detail"] == (
        "Maia configuration is invalid. Review SCAN64_MAIA_CONFIG."
    )

def test_low_rating_maia_selection_persists_coverage_disclosure(db_session: Session) -> None:
    service = PlaySessionService(
        db_session=db_session,
        stockfish_provider=StockfishOpponentProvider(StockfishConfig()),
        maia_config=MaiaConfig(
            binary_path=Path("/operator/lc0"),
            checkpoints=(
                MaiaCheckpoint(
                    rating=1100,
                    weights_path=Path("/operator/maia-1100.pb.gz"),
                ),
            ),
        ),
    )
    play_session = PlaySession(
        player_id="maia-player",
        opponent_config={"provider": "maia", "strength": "900"},
    )

    service.persist_maia_selection(play_session, strength_setting=900)

    assert play_session.opponent_config["maia_checkpoint"] == "1100"
    assert play_session.opponent_config["maia_coverage_disclosure"] == (
        "Maia has no checkpoint below 1100; requested rating 900 uses the 1100 checkpoint."
    )


def test_in_range_maia_selection_persists_granularity_disclosure(db_session: Session) -> None:
    service = PlaySessionService(
        db_session=db_session,
        stockfish_provider=StockfishOpponentProvider(StockfishConfig()),
        maia_config=MaiaConfig(
            binary_path=Path("/operator/lc0"),
            checkpoints=(
                MaiaCheckpoint(
                    rating=1100,
                    weights_path=Path("/operator/maia-1100.pb.gz"),
                ),
                MaiaCheckpoint(
                    rating=1500,
                    weights_path=Path("/operator/maia-1500.pb.gz"),
                ),
            ),
        ),
    )
    play_session = PlaySession(
        player_id="maia-player",
        opponent_config={"provider": "maia", "strength": "1400"},
    )

    service.persist_maia_selection(play_session, strength_setting=1400)

    assert play_session.opponent_config["maia_checkpoint"] == "1500"
    assert play_session.opponent_config["maia_coverage_disclosure"] == (
        "Maia checkpoints use approximately 100-Elo granularity; requested rating "
        "1400 uses the nearest 1500 checkpoint."
    )

