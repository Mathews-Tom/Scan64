"""Variant engine lines on the Analysis Board through the real app and engine pool (#398).

The engine is ``tests/fixtures/engines/fake_uci.py`` behind a platform launcher,
declaring ``UCI_Variant`` from ``FAKE_UCI_VARIANTS`` like Fairy-Stockfish.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import chess
import chess.variant
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import scan64.persistence.database as db_module
from scan64.api.app import app
from scan64.providers.engine import ENGINE_CONFIG_ENV
from tests.support.engines import fake_uci_launcher

CRAZYHOUSE = "rnbqkbnr/ppp1pppp/8/8/8/8/PPPP1PPP/RNBQKBNR[Pp] w KQkq - 0 3"


@pytest.fixture
def app_session(db_session: Session) -> Iterator[Session]:
    app.dependency_overrides[db_module.get_session] = lambda: db_session
    try:
        yield db_session
    finally:
        app.dependency_overrides.clear()


def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variants: str) -> Path:
    monkeypatch.setenv("FAKE_UCI_VARIANTS", variants)
    monkeypatch.setenv("FAKE_UCI_MULTIPV", "1")
    launcher, log = fake_uci_launcher(tmp_path, monkeypatch)
    profile = tmp_path / "variant-engine.toml"
    profile.write_text(
        f"[engine]\nname = \"variant\"\nbinary_path = '{launcher}'\nthreads = 1\nhash_mb = 16\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(profile))
    return log


def _analyse(client: TestClient, fen: str, variant: str) -> tuple[int, dict[str, object]]:
    player_id = f"variant-{uuid4()}"
    created = client.post("/v1/players", json={"id": player_id})
    assert created.status_code == 200, created.text
    client.headers["Authorization"] = f"Bearer {created.json()['access_token']}"
    response = client.post(
        "/v1/analysis/position", json={"player_id": player_id, "fen": fen, "variant": variant}
    )
    return response.status_code, response.json()


def test_a_variant_engine_gives_lines_under_the_variants_rules(
    app_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _profile(tmp_path, monkeypatch, "chess,crazyhouse")
    with TestClient(app) as client:
        status, body = _analyse(client, CRAZYHOUSE, "crazyhouse")
    assert status == 200, body
    assert body["variant"] == "crazyhouse"
    board = chess.variant.CrazyhouseBoard(CRAZYHOUSE)
    first = body["lines"][0]["moves"][0]  # type: ignore[index]
    assert board.san(chess.Move.from_uci(first["uci"])) == first["san"]
    assert "setoption name UCI_Variant value crazyhouse" in log.read_text(encoding="utf-8")


def test_an_engine_without_the_variant_answers_503_with_the_reason(
    app_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _profile(tmp_path, monkeypatch, "")
    with TestClient(app) as client:
        status, body = _analyse(client, CRAZYHOUSE, "crazyhouse")
    assert status == 503, body
    detail = body["detail"]
    assert detail["code"] == "engine_capability_unavailable"  # type: ignore[index]
    assert "Fairy-Stockfish" in detail["reason"]  # type: ignore[index]
    assert "go" not in log.read_text(encoding="utf-8").split()


def test_a_fen_invalid_under_the_variant_is_refused_before_the_engine(
    app_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(tmp_path, monkeypatch, "chess,horde")
    with TestClient(app) as client:
        # A standard start position has kings on both sides: not a horde position.
        status, body = _analyse(client, chess.STARTING_FEN, "horde")
    assert status == 422, body


def test_standard_analysis_still_answers_without_a_variant_field(
    app_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(tmp_path, monkeypatch, "")
    with TestClient(app) as client:
        player_id = f"variant-{uuid4()}"
        created = client.post("/v1/players", json={"id": player_id})
        client.headers["Authorization"] = f"Bearer {created.json()['access_token']}"
        response = client.post(
            "/v1/analysis/position", json={"player_id": player_id, "fen": chess.STARTING_FEN}
        )
    assert response.status_code == 200, response.json()
    assert response.json()["variant"] == "chess"


def test_an_explanation_of_variant_lines_is_refused_not_given_under_standard_rules(
    app_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _profile(tmp_path, monkeypatch, "chess,atomic")
    with TestClient(app) as client:
        player_id = f"variant-{uuid4()}"
        created = client.post("/v1/players", json={"id": player_id})
        client.headers["Authorization"] = f"Bearer {created.json()['access_token']}"
        response = client.post(
            "/v1/analysis/position/explanation",
            json={"player_id": player_id, "fen": chess.STARTING_FEN, "variant": "atomic"},
        )
    assert response.status_code == 422, response.json()
    assert "standard chess only, not atomic" in response.json()["detail"]
    # Refused before the pool: the engine never searched.
    assert not log.exists() or "go" not in log.read_text(encoding="utf-8").split()
