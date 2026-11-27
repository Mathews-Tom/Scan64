from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.games.models import Game, PlaySession
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import PooledStockfishAdapter
from tests.support.engines import fake_xboard_launcher

ENDPOINT = "/v1/analysis/position"


def register(session: Session, player_id: str) -> dict[str, str]:
    token, hashed = issue_player_token()
    session.add(Player(id=player_id))
    session.add(PlayerCredential(player_id=player_id, token_hash=hashed))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_authentication_precedes_fen_validation_and_engine_access(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(db_session, "analysis-owner")
    other = register(db_session, "analysis-other")
    analyze = AsyncMock()
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    body = {"player_id": "analysis-owner", "fen": "not a FEN"}
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        response = client.post(ENDPOINT, headers=headers, json=body)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
    foreign = client.post(ENDPOINT, headers=other, json=body)
    missing = client.post(ENDPOINT, headers=owner, json={**body, "player_id": "missing"})
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "Player not found"}
    analyze.assert_not_awaited()


@pytest.mark.parametrize("fen", ["not a FEN", "8/8/8/8/8/8/8/8 w - - 0 1"])
def test_invalid_positions_do_not_search(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, fen: str
) -> None:
    headers = register(db_session, "analysis-owner")
    analyze = AsyncMock()
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(
        ENDPOINT, headers=headers, json={"player_id": "analysis-owner", "fen": fen}
    )
    assert response.status_code == 422
    analyze.assert_not_awaited()


@pytest.mark.parametrize("turn", [chess.WHITE, chess.BLACK])
@pytest.mark.parametrize(
    "score",
    [chess.engine.Cp(45), chess.engine.Cp(-32), chess.engine.Mate(3), chess.engine.Mate(-2)],
)
def test_real_adapter_scores_are_normalized_to_white(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    turn: chess.Color,
    score: chess.engine.Score,
    tmp_path: Path,
) -> None:
    headers = register(db_session, "analysis-owner")
    board = chess.Board()
    board.turn = turn
    pv = ["e2e4", "e7e5"] if turn else ["e7e5", "e2e4"]
    engine = AsyncMock()
    engine.analyse.return_value = [
        {"score": chess.engine.PovScore(score, turn), "pv": [chess.Move.from_uci(m) for m in pv]}
    ]
    engine.id = {"name": "deterministic"}
    engine.config = {}
    engine.options = {}
    binary = tmp_path / "stockfish"
    binary.write_bytes(b"deterministic UCI test double")
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    adapter = PooledStockfishAdapter(StockfishConfig(binary_path=str(binary)))
    analyze = AsyncMock(side_effect=adapter.analyze_position)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(
        ENDPOINT, headers=headers, json={"player_id": "analysis-owner", "fen": board.fen()}
    )
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    white_score = score if turn else -score
    assert line["score_cp"] == white_score.score()
    assert line["mate"] == white_score.mate()
    assert line["moves"] == [
        {"uci": pv[0], "san": "e4" if turn else "e5"},
        {"uci": pv[1], "san": "e5" if turn else "e4"},
    ]
    analyze.assert_awaited_once_with(board)


@pytest.mark.parametrize(
    ("fen", "pv", "sans"),
    [
        (chess.STARTING_FEN, ["e2e4", "e7e5", "g1f3"], ["e4", "e5", "Nf3"]),
        (" ".join(chess.STARTING_FEN.split()[:4]), ["e2e4"], ["e4"]),
        (chess.STARTING_FEN.replace("0 1", "100 80"), ["e2e4"], ["e4"]),
        ("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1", ["f7g7"], ["Qg7#"]),
        ("8/8/8/8/8/6k1/5q2/7K b - - 0 1", ["f2g2"], ["Qg2#"]),
        ("7k/P7/8/8/8/8/8/7K w - - 0 1", ["a7a8q"], ["a8=Q+"]),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", ["e8g8"], ["O-O"]),
        ("7k/8/8/3pP3/8/8/8/7K w - d6 0 2", ["e5d6"], ["exd6"]),
    ],
)
def test_pv_moves_are_legal_and_have_contextual_san(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fen: str,
    pv: list[str],
    sans: list[str],
) -> None:
    headers = register(db_session, "analysis-owner")
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": pv, "score_cp": 0}]))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(
        ENDPOINT, headers=headers, json={"player_id": "analysis-owner", "fen": fen}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fen"] == chess.Board(fen).fen()
    assert body["lines"][0]["moves"] == [
        {"uci": uci, "san": san} for uci, san in zip(pv, sans, strict=True)
    ]
    board = chess.Board(body["fen"])
    for move in body["lines"][0]["moves"]:
        parsed = board.parse_uci(move["uci"])
        assert parsed in board.legal_moves
        assert board.san(parsed) == move["san"]
        board.push(parsed)


def test_filters_entire_invalid_lines_and_caps_ranked_lines_without_mutating_analysis(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session, "analysis-owner")
    raw: list[dict[str, Any]] = [
        {"pv": ["e2e4", "e2e3"], "score_cp": 100},
        {"pv": ["0000"], "score_cp": 90},
        {"pv": ["e2e4"], "score_cp": 50, "score_mate": 2},
        {"pv": ["e2e4"], "score_cp": True},
        {"pv": ["e2e4"], "score_cp": "50"},
        {"pv": ["e2e4"]},
        {"pv": ["e2e4", 123], "score_cp": 40},
        {"pv": ["e2e4"], "score_cp": 30},
        {"pv": ["d2d4"], "score_cp": 20},
        {"pv": ["g1f3"], "score_cp": 10},
        {"pv": ["c2c4"], "score_cp": 0},
    ]
    analysis = EngineAnalysis(raw_result=raw)
    before = analysis.model_dump()
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint", AsyncMock(return_value=analysis)
    )
    response = client.post(
        ENDPOINT, headers=headers, json={"player_id": "analysis-owner", "fen": chess.STARTING_FEN}
    )
    assert response.status_code == 200, response.text
    assert [line["score_cp"] for line in response.json()["lines"]] == [30, 20, 10]
    assert analysis.model_dump() == before


@pytest.mark.parametrize(
    "raw",
    [
        [],
        [{"pv": [], "score_cp": 0}],
        [{"pv": ["e2e5"], "score_cp": 0}],
        [{"pv": ["e2e4"]}],
        [{"score_cp": 0}],
    ],
)
def test_unusable_nonterminal_output_is_unavailable(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    raw: list[dict[str, Any]],
) -> None:
    headers = register(db_session, "analysis-owner")
    monkeypatch.setattr(
        app.state.engine_pool_manager,
        "analyze_hint",
        AsyncMock(return_value=EngineAnalysis(raw_result=raw)),
    )
    response = client.post(
        ENDPOINT, headers=headers, json={"player_id": "analysis-owner", "fen": chess.STARTING_FEN}
    )
    assert response.status_code == 503
    assert "try again" in response.json()["detail"]


def test_manual_retry_replaces_unusable_cached_output_and_reuses_success(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    headers = register(db_session, "analysis-owner")
    engine = AsyncMock()
    engine.id = {"name": "deterministic"}
    engine.config = {}
    engine.options = {}
    binary = tmp_path / "stockfish"
    binary.write_bytes(b"deterministic UCI test double")
    monkeypatch.setattr(app.state.engine_pool_manager.config, "binary_path", str(binary))
    score = chess.engine.PovScore(chess.engine.Cp(25), chess.WHITE)
    engine.analyse.side_effect = [
        [{"score": score, "pv": [chess.Move.from_uci("e2e4"), chess.Move.from_uci("e2e3")]}],
        [{"score": score, "pv": [chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5")]}],
    ]
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    body = {"player_id": "analysis-owner", "fen": chess.STARTING_FEN}

    failed = client.post(ENDPOINT, headers=headers, json=body)
    assert failed.status_code == 503
    assert "try again" in failed.json()["detail"]
    assert engine.analyse.await_count == 1

    retried = client.post(ENDPOINT, headers=headers, json=body)
    assert retried.status_code == 200, retried.text
    assert retried.json() == {
        "fen": chess.STARTING_FEN,
        "lines": [
            {
                "score_cp": 25,
                "mate": None,
                "moves": [{"uci": "e2e4", "san": "e4"}, {"uci": "e7e5", "san": "e5"}],
            }
        ],
    }
    assert engine.analyse.await_count == 2

    cached = client.post(ENDPOINT, headers=headers, json=body)
    assert cached.status_code == 200, cached.text
    assert cached.json() == retried.json()
    assert engine.analyse.await_count == 2


@pytest.mark.parametrize(
    "fen",
    [
        "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1",
        "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",
        "7k/8/6K1/8/8/8/8/8 w - - 0 1",
        chess.STARTING_FEN.replace("0 1", "150 80"),
    ],
)
def test_terminal_positions_need_no_pool_or_search(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, fen: str
) -> None:
    headers = register(db_session, "analysis-owner")
    analyze = AsyncMock()
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    body = {"player_id": "analysis-owner", "fen": fen}
    for disabled in (False, True):
        if disabled:
            monkeypatch.setattr(app.state, "engine_pool_manager", None)
        response = client.post(ENDPOINT, headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.json() == {"fen": chess.Board(fen).fen(), "lines": []}
    analyze.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ValueError("bad output"),
        chess.engine.EngineError("failed"),
        chess.engine.EngineTerminatedError("closed"),
        OSError("unavailable"),
    ],
)
def test_engine_boundary_failures_are_retryable(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    headers = register(db_session, "analysis-owner")
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", AsyncMock(side_effect=error))
    response = client.post(
        ENDPOINT, headers=headers, json={"player_id": "analysis-owner", "fen": chess.STARTING_FEN}
    )
    assert response.status_code == 503
    assert "try again" in response.json()["detail"]


def test_hint_admission_busy_and_pool_unavailable_are_explicit(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session, "analysis-owner")
    manager = app.state.engine_pool_manager
    monkeypatch.setattr(manager, "_hint_in_progress", True)
    body = {"player_id": "analysis-owner", "fen": chess.STARTING_FEN}
    busy = client.post(ENDPOINT, headers=headers, json=body)
    assert busy.status_code == 503
    assert "busy" in busy.json()["detail"]
    assert "try again" in busy.json()["detail"]
    monkeypatch.setattr(manager.interactive_pool, "concurrency", 1)
    single = client.post(ENDPOINT, headers=headers, json=body)
    assert single.status_code == 503
    assert "at least two" in single.json()["detail"]
    monkeypatch.setattr(app.state, "engine_pool_manager", None)
    absent = client.post(ENDPOINT, headers=headers, json=body)
    assert absent.status_code == 503
    assert "interactive engine pool" in absent.json()["detail"]


def test_analysis_is_read_only_and_response_contains_no_player_tokens(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(db_session, "analysis-owner")
    other = register(db_session, "analysis-other")
    game = Game(pgn="*", owner_player_id="analysis-owner", owner_color="white")
    db_session.add(game)
    db_session.flush()
    db_session.add(PlaySession(player_id="analysis-owner", game_id=game.id))
    db_session.commit()
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 25}]))
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    writes: list[str] = []

    def record_statement(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        responses = [
            client.post(
                ENDPOINT, headers=headers, json={"player_id": player, "fen": chess.STARTING_FEN}
            )
            for player, headers in [("analysis-owner", owner), ("analysis-other", other)]
        ]
        terminal = client.post(
            ENDPOINT,
            headers=owner,
            json={"player_id": "analysis-owner", "fen": "7k/8/6K1/8/8/8/8/8 w - - 0 1"},
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)
    assert all(response.status_code == 200 for response in responses)
    assert terminal.status_code == 200
    assert responses[0].json() == responses[1].json()
    assert set(responses[0].json()) == {"fen", "lines"}
    for response in responses:
        assert owner["Authorization"].removeprefix("Bearer ") not in response.text
        assert other["Authorization"].removeprefix("Bearer ") not in response.text
    assert writes == []
    assert analyze.await_count == 2


def test_engine_without_multipv_answers_capability_unavailable_not_500(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A real XBoard process cannot give the hint's lines: a stable 503, never a 500."""
    launcher, _ = fake_xboard_launcher(tmp_path, monkeypatch)
    headers = register(db_session, "analysis-owner")
    manager = app.state.engine_pool_manager
    monkeypatch.setattr(manager.config, "binary_path", str(launcher))
    monkeypatch.setattr(manager.config, "protocol", "xboard")
    assert manager.hint_limits()[2] > 1
    response = client.post(
        ENDPOINT, headers=headers, json={"player_id": "analysis-owner", "fen": chess.STARTING_FEN}
    )
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "engine_capability_unavailable"
    assert "MultiPV" in detail["reason"]
    assert "FakeXBoard 1" in detail["reason"]
