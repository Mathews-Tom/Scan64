"""A second open-source UCI engine runs the full analysis path by configuration only.

The engine binary is not bundled. Build Ethereal (GPL-3.0) as described in
docs/engines.md, or point ``SCAN64_TEST_SECOND_ENGINE`` at any UCI engine.
Outside CI the tests skip when neither exists. In CI (``CI`` set, as on GitHub
Actions) a missing engine or Stockfish fails the tests instead. CI points
``SCAN64_TEST_SECOND_ENGINE`` at ``tests/fixtures/scripted_uci_engine.py``.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

import scan64.persistence.database as db_module
from scan64.api.app import app
from scan64.chess.analysis.jobs import execute_analysis_job_async
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, GameMoveReview
from scan64.chess.positions.models import Position
from scan64.providers.engine import ENGINE_CONFIG_ENV

PGN = """[Event "Second engine"]
[White "Owner"]
[Black "Opponent"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 d6 4. O-O h6 5. d4 exd4 6. Nxd4 Nxd4 7. Qxd4 c5 8. Qd5
Be6 9. Bb5+ Bd7 10. Bxd7+ Qxd7 1-0"""


IN_CI = bool(os.environ.get("CI"))


def _second_engine() -> Path | None:
    configured = os.environ.get("SCAN64_TEST_SECOND_ENGINE")
    if configured:
        # An explicit path that does not exist is a configuration error, never a skip.
        return Path(configured)
    candidates: list[str] = []
    home = Path.home() / ".local" / "opt" / "engines"
    candidates += [str(home / "Ethereal.exe"), str(home / "Ethereal")]
    for candidate in candidates:
        if Path(candidate).is_file():
            return Path(candidate)
    return None


SECOND_ENGINE = _second_engine()
pytestmark = pytest.mark.skipif(
    SECOND_ENGINE is None and not IN_CI, reason="second UCI engine binary is not installed"
)
# tests/conftest.py removes SCAN64_STOCKFISH_PATH, so the default profile runs
# the Stockfish on PATH.
STOCKFISH = shutil.which("stockfish")


@pytest.fixture
def ethereal_profile(tmp_path: Path) -> Path:
    if SECOND_ENGINE is None:
        pytest.fail("CI is set but no second UCI engine: set SCAN64_TEST_SECOND_ENGINE")
    if not SECOND_ENGINE.is_file():
        pytest.fail(f"SCAN64_TEST_SECOND_ENGINE is not a file: {SECOND_ENGINE}")
    path = tmp_path / "second-engine.toml"
    path.write_text(
        f"""[engine]
name = "second"
binary_path = '{SECOND_ENGINE}'
threads = 1
hash_mb = 16
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def app_session(db_session: Session) -> Iterator[Session]:
    app.dependency_overrides[db_module.get_session] = lambda: db_session
    try:
        yield db_session
    finally:
        app.dependency_overrides.clear()


def _analyse_stored_game(client: TestClient, session: Session, player_id: str) -> UUID:
    created = client.post(
        "/v1/games", json={"owner_color": "white", "pgn": PGN, "player_id": player_id}
    )
    assert created.status_code == 200, created.text
    game_id = UUID(created.json()["id"])
    job = client.post(f"/v1/games/{game_id}/analysis-jobs")
    assert job.status_code == 200, job.text
    job_id = UUID(job.json()["id"])
    assert client.portal is not None
    # The production path: the lifespan-bound pool built from the active profile.
    client.portal.call(execute_analysis_job_async, job_id, app.state.engine_pool_manager)
    status = client.get(f"/v1/analysis-jobs/{job_id}")
    if status.json()["status"] != "completed":
        session.expire_all()
        stored = session.get(AnalysisJob, job_id)
        pytest.fail(f"analysis job {status.json()}: {stored.error if stored else 'missing'}")
    return game_id


def _rows(session: Session, game_id: UUID) -> list[EngineAnalysis]:
    session.expire_all()
    return list(
        session.exec(
            select(EngineAnalysis)
            .join(Position, col(Position.id) == col(EngineAnalysis.position_id))
            .where(Position.game_id == game_id)
        ).all()
    )


def _login(client: TestClient) -> str:
    player_id = f"second-engine-{uuid4()}"
    response = client.post("/v1/players", json={"id": player_id})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return player_id


def test_second_engine_analyses_stored_game_through_config_only(
    app_session: Session, ethereal_profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(ethereal_profile))
    with TestClient(app) as client:
        player_id = _login(client)
        token = client.headers["Authorization"]
        game_id = _analyse_stored_game(client, app_session, player_id)

    rows = _rows(app_session, game_id)
    assert rows
    for row in rows:
        assert not row.config["engine_name"].startswith("Stockfish"), row.config
        assert row.config["engine_version"].startswith("sha256:")
        # Stockfish-only strength controls are neither sent nor recorded.
        assert "Skill Level" not in row.config["options"]
        for line in row.lines():
            assert line.pv and (line.score_cp is not None or line.score_mate is not None)
            assert line.depth is not None and line.seldepth is not None
            assert line.nodes is not None and line.multipv is not None
    reviews = app_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == game_id)
    ).all()
    assert len(reviews) == 20

    # Interactive MultiPV analysis (the hint pool) through the same profile.
    with TestClient(app) as client:
        client.headers["Authorization"] = token
        response = client.post(
            "/v1/analysis/position",
            json={"player_id": player_id, "fen": chess.STARTING_FEN},
        )
    assert response.status_code == 200, response.text
    lines = response.json()["lines"]
    assert len(lines) > 1 and all(line["moves"] for line in lines)


@pytest.mark.skipif(STOCKFISH is None and not IN_CI, reason="Stockfish is not installed")
def test_second_engine_never_reuses_stockfish_rows(
    app_session: Session, ethereal_profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if STOCKFISH is None:
        pytest.fail("CI is set but Stockfish is not installed")
    monkeypatch.delenv(ENGINE_CONFIG_ENV, raising=False)
    with TestClient(app) as client:
        player_id = _login(client)
        token = client.headers["Authorization"]
        stockfish_game = _analyse_stored_game(client, app_session, player_id)
    stockfish_names = {row.config["engine_name"] for row in _rows(app_session, stockfish_game)}
    assert stockfish_names and all(name.startswith("Stockfish") for name in stockfish_names)

    # Same owner and same positions: exact-key batch reuse is in scope, but a
    # different executable digest must never match a Stockfish row.
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(ethereal_profile))
    with TestClient(app) as client:
        client.headers["Authorization"] = token
        second_game = _analyse_stored_game(client, app_session, player_id)
    second_rows = _rows(app_session, second_game)
    assert second_rows
    assert not any(row.config["engine_name"].startswith("Stockfish") for row in second_rows)
    stockfish_versions = {
        row.config["engine_version"] for row in _rows(app_session, stockfish_game)
    }
    assert stockfish_versions.isdisjoint({row.config["engine_version"] for row in second_rows})
