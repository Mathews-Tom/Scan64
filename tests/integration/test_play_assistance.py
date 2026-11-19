"""Owned pre-move disclosures, legal grading witnesses and shared admission."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.games.assistance import (
    PlaySnapshot,
    SessionHintReveal,
    claim_snapshot,
    persist_assistance,
)
from scan64.chess.games.models import Game, PlaySession
from scan64.learning.play_assistance import grade_play_moves
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import (
    HINT_MULTIPV,
    HINT_NODES,
    HINT_TIME_MS,
    EnginePoolManager,
    PooledStockfishAdapter,
)

OWNER = "play-assistance-owner"


def _register(session: Session, player_id: str = OWNER) -> dict[str, str]:
    token, hashed = issue_player_token()
    session.add(Player(id=player_id))
    session.add(PlayerCredential(player_id=player_id, token_hash=hashed))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


def _source(
    session: Session, *, fen: str = chess.STARTING_FEN, moves: list[str] | None = None
) -> tuple[PlaySession, Game, chess.Board]:
    board = chess.Board(fen)
    for uci in moves or []:
        board.push_uci(uci)
    game = Game(pgn="*", headers={"FEN": fen}, moves=moves or [], owner_player_id=OWNER)
    session.add(game)
    session.flush()
    play = PlaySession(
        player_id=OWNER,
        game_id=game.id,
        assistance_tracking_version=1,
        opponent_config={
            "player_color": "white" if board.turn else "black",
            "assistance_generation": "0",
        },
    )
    session.add(play)
    session.commit()
    return play, game, board


def _request(
    client: TestClient,
    play: PlaySession,
    headers: dict[str, str],
    *,
    fen: str = chess.STARTING_FEN,
    moves: list[str] | None = None,
    mode: str = "candidates",
    player_id: str = OWNER,
) -> Response:
    return client.post(
        f"/v1/play-sessions/{play.id}/assistance",
        headers=headers,
        json={
            "player_id": player_id,
            "fen": fen,
            "moves": ["e2e4"] if moves is None else moves,
            "mode": mode,
        },
    )


def _witness(fen: str, score: int = 0) -> EngineAnalysis:
    board = chess.Board(fen)
    return EngineAnalysis(
        raw_result=[{"pv": [next(iter(board.legal_moves)).uci()], "score_cp": score}]
    )


def test_assistance_does_not_disclose_foreign_or_missing_sources(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _register(db_session)
    other = _register(db_session, "play-assistance-other")
    play, _, _ = _source(db_session)
    analyze = AsyncMock()
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    assert _request(client, play, {}).status_code == 401
    assert _request(client, play, {"Authorization": "Bearer invalid"}).status_code == 401
    mismatch = _request(client, play, other)
    unknown_actor = _request(client, play, owner, player_id="missing")
    assert mismatch.status_code == unknown_actor.status_code == 404
    assert mismatch.json() == unknown_actor.json()
    # Ownership is checked before even parsing the supplied position or moves.
    foreign = _request(
        client, play, other, player_id="play-assistance-other", fen="bad", moves=["bad"]
    )
    missing = client.post(
        f"/v1/play-sessions/{uuid4()}/assistance",
        headers=other,
        json={
            "player_id": "play-assistance-other",
            "fen": "bad",
            "moves": ["bad"],
            "mode": "guard",
        },
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    analyze.assert_not_awaited()
    assert db_session.exec(select(SessionHintReveal)).all() == []


@pytest.mark.parametrize(
    ("fen", "moves", "mode", "status"),
    [
        (chess.STARTING_FEN, [], "candidates", 422),
        (chess.STARTING_FEN, ["e2e4", "e2e4"], "candidates", 422),
        (chess.STARTING_FEN, ["e2e4", "d2d4", "c2c4", "g1f3"], "candidates", 422),
        (chess.STARTING_FEN, ["e2e4", "d2d4"], "guard", 422),
        (chess.STARTING_FEN, ["0000"], "guard", 422),
        (chess.STARTING_FEN, ["e2e5"], "candidates", 422),
        (chess.STARTING_FEN, ["not-uci"], "candidates", 422),
        ("bad", ["e2e4"], "candidates", 422),
        ("8/8/8/8/8/8/8/8 w - - 0 1", ["e2e4"], "candidates", 422),
        (" ".join(chess.STARTING_FEN.split()[:4]), ["e2e4"], "candidates", 422),
        (chess.STARTING_FEN.replace("0 1", "1 1"), ["e2e4"], "guard", 409),
        (chess.STARTING_FEN.replace("0 1", "0 2"), ["e2e4"], "guard", 409),
    ],
)
def test_invalid_or_counter_stale_proposals_never_reach_engine(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fen: str,
    moves: list[str],
    mode: str,
    status: int,
) -> None:
    headers = _register(db_session)
    play, game, _ = _source(db_session)
    analyze = AsyncMock()
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = _request(client, play, headers, fen=fen, moves=moves, mode=mode)
    assert response.status_code == status, response.text
    assert set(response.json()) == {"detail"}
    analyze.assert_not_awaited()
    assert game.moves == []
    assert db_session.exec(select(SessionHintReveal)).all() == []


@pytest.mark.parametrize(
    "change", ["completed", "result", "opponent_turn", "terminal", "bad_replay", "null_replay"]
)
def test_ineligible_authoritative_source_never_reaches_engine(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    headers = _register(db_session)
    play, game, _ = _source(db_session)
    if change == "completed":
        play.status = "completed"
    elif change == "result":
        game.result = "1-0"
    elif change == "opponent_turn":
        play.opponent_config = {**play.opponent_config, "player_color": "black"}
    elif change == "terminal":
        game.headers = {"FEN": "7k/8/6K1/8/8/8/8/8 w - - 0 1"}
    elif change == "null_replay":
        game.moves = ["0000"]
    else:
        game.moves = ["e2e5"]
    db_session.commit()
    analyze = AsyncMock()
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = _request(client, play, headers)
    assert response.status_code == 409, response.text
    assert set(response.json()) == {"detail"}
    analyze.assert_not_awaited()
    assert db_session.exec(select(SessionHintReveal)).all() == []


def test_both_modes_persist_before_disclosure_without_creating_or_moving_game(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _register(db_session)
    play = PlaySession(
        player_id=OWNER,
        assistance_tracking_version=1,
        opponent_config={"assistance_generation": "0"},
    )
    db_session.add(play)
    db_session.commit()
    analyze = AsyncMock(side_effect=_witness)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    for mode, generation, modes in [
        ("candidates", 1, ["candidates"]),
        ("guard", 2, ["candidates", "guard"]),
    ]:
        response = _request(client, play, headers, mode=mode)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["generation"] == generation
        assert body["assistance_modes"] == modes
        assert body["grades"] == [
            {"move": "e2e4", "san": "e4", "band": "sound", "win_probability_loss": 0.0}
        ]
        db_session.expire_all()
        stored = db_session.get(PlaySession, play.id)
        assert stored is not None
        assert stored.game_id is None
        assert stored.opponent_config["assistance_generation"] == str(generation)
        reveal = db_session.exec(select(SessionHintReveal)).one()
        assert reveal.highest_step == 0
        assert reveal.effective_step == 3
        assert reveal.candidate_used is True
        assert reveal.guard_used == (mode == "guard")
        assert reveal.moves == []
        assert db_session.exec(select(Game)).all() == []


@pytest.mark.parametrize("mutation", ["moves", "completed", "initial_fen", "assistance", "aba"])
def test_snapshot_race_discloses_no_grades_or_new_provenance(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    headers = _register(db_session)
    play, game, board = _source(db_session, moves=["g1f3", "g8f6"])
    changed = False

    async def racing_engine(fen: str) -> EngineAnalysis:
        nonlocal changed
        if not changed:
            changed = True
            if mutation == "moves":
                game.moves = [*game.moves, "d2d4", "d7d5"]
            elif mutation == "completed":
                play.status = "completed"
            elif mutation == "initial_fen":
                game.headers = {"FEN": chess.STARTING_FEN.replace("0 1", "7 4")}
            elif mutation == "assistance":
                persist_assistance(db_session, PlaySnapshot.capture(play, game), board, 1)
            else:
                original = list(game.moves)
                claim_snapshot(db_session, PlaySnapshot.capture(play, game))
                game.moves = []
                db_session.commit()
                claim_snapshot(db_session, PlaySnapshot.capture(play, game))
                game.moves = original
            db_session.commit()
        return _witness(fen)

    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", racing_engine)
    response = _request(client, play, headers, fen=board.fen(), mode="guard")
    assert response.status_code == 409, response.text
    assert set(response.json()) == {"detail"}
    reveals = db_session.exec(select(SessionHintReveal)).all()
    assert len(reveals) == int(mutation == "assistance")
    assert all(not row.candidate_used and not row.guard_used for row in reveals)


@pytest.mark.parametrize("stage", ["source", "reply"])
@pytest.mark.parametrize(
    "raw",
    [
        [],
        [{"pv": ["e2e4"]}],
        [{"pv": [], "score_cp": 0}],
        [{"pv": ["0000"], "score_cp": 0}],
        [{"pv": ["a1a8"], "score_cp": 0}],
        [{"pv": ["e2e4", "e2e5"], "score_cp": 0}],
        [{"pv": ["e2e4"], "score_cp": True}],
        [{"pv": ["e2e4"], "score_cp": "0"}],
        [{"pv": ["e2e4"], "score_cp": float("nan")}],
        [{"pv": ["e2e4"], "score_mate": 0}],
        [{"pv": ["e2e4"], "score_cp": 0, "score_mate": 1}],
    ],
)
def test_bad_source_or_reply_witness_never_discloses_or_counts_assistance(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    raw: list[dict[str, Any]],
) -> None:
    headers = _register(db_session)
    play, game, _ = _source(db_session)
    if stage == "reply":
        raw = [
            {**line, "pv": ["e7e5" if uci == "e2e4" else uci for uci in line.get("pv", [])]}
            for line in raw
        ]
    invalid = EngineAnalysis(raw_result=raw)
    analyze = AsyncMock(
        side_effect=[invalid] if stage == "source" else [_witness(chess.STARTING_FEN), invalid]
    )
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = _request(client, play, headers, mode="guard")
    assert response.status_code == 503, response.text
    assert set(response.json()) == {"detail"}
    assert analyze.await_count == (1 if stage == "source" else 2)
    assert db_session.exec(select(SessionHintReveal)).all() == []
    assert game.moves == []
    assert play.opponent_config["assistance_generation"] == "0"


@pytest.mark.parametrize(
    "failure", ["no_pool", "single_engine", "busy", "reply_busy", "engine_error"]
)
def test_capacity_or_engine_failure_returns_no_partial_grades_or_provenance(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    headers = _register(db_session)
    play, _, _ = _source(db_session)
    manager = app.state.engine_pool_manager
    if failure == "no_pool":
        monkeypatch.setattr(app.state, "engine_pool_manager", None)
    elif failure == "single_engine":
        monkeypatch.setattr(manager.interactive_pool, "concurrency", 1)
    elif failure == "busy":
        monkeypatch.setattr(manager, "_hint_in_progress", True)
    else:
        branch = chess.Board()
        branch.push_uci("e2e4")
        outcomes = (
            [_witness(chess.STARTING_FEN), _witness(branch.fen()), TimeoutError("busy")]
            if failure == "reply_busy"
            else [chess.engine.EngineError("failed")]
        )
        monkeypatch.setattr(manager, "analyze_hint", AsyncMock(side_effect=outcomes))
    response = _request(
        client, play, headers, moves=["e2e4", "d2d4"] if failure == "reply_busy" else ["e2e4"]
    )
    assert response.status_code == 503, response.text
    assert set(response.json()) == {"detail"}
    assert db_session.exec(select(SessionHintReveal)).all() == []
    assert play.opponent_config["assistance_generation"] == "0"


def test_rejected_cached_witness_is_evicted_without_hidden_retry_or_failed_disclosure(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _register(db_session)
    play, _, _ = _source(db_session)
    branch = chess.Board()
    branch.push_uci("e2e4")
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    analyze = AsyncMock(
        side_effect=[
            _witness(chess.STARTING_FEN),
            EngineAnalysis(raw_result=[{"pv": ["0000"], "score_cp": 0}]),
            _witness(branch.fen()),
        ]
    )
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    failed = _request(client, play, headers, mode="guard")
    assert failed.status_code == 503, failed.text
    assert set(failed.json()) == {"detail"}
    assert analyze.await_count == 2
    assert db_session.exec(select(SessionHintReveal)).all() == []
    retried = _request(client, play, headers, mode="guard")
    assert retried.status_code == 200, retried.text
    assert retried.json()["generation"] == 1
    assert retried.json()["assistance_modes"] == ["guard"]
    assert analyze.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("turn", [chess.WHITE, chess.BLACK])
@pytest.mark.parametrize(
    ("reply", "band"),
    [(600, "blunder"), (200, "mistake"), (100, "inaccuracy"), (0, "sound"), (-600, "sound")],
)
async def test_branch_scores_use_opponent_perspective(
    monkeypatch: pytest.MonkeyPatch, turn: chess.Color, reply: int, band: str
) -> None:
    board = chess.Board()
    if turn == chess.BLACK:
        board.push_uci("e2e4")
    move = next(iter(board.legal_moves))
    branch = board.copy()
    branch.push(move)
    manager = EnginePoolManager(StockfishConfig())
    analyze = AsyncMock(side_effect=[_witness(board.fen()), _witness(branch.fen(), reply)])
    monkeypatch.setattr(manager, "analyze_hint", analyze)
    original_fen, original_moves = board.fen(), list(board.move_stack)
    grades = await grade_play_moves(board, [move], manager)
    assert grades[0].band == band
    if reply > 0:
        assert grades[0].win_probability_loss > 0.08
    else:
        assert grades[0].win_probability_loss == 0
    assert board.fen() == original_fen
    assert board.move_stack == original_moves


@pytest.mark.asyncio
@pytest.mark.parametrize(("mate", "band"), [(1, "blunder"), (-1, "sound")])
async def test_reply_mate_scores_remain_from_the_reply_movers_perspective(
    monkeypatch: pytest.MonkeyPatch, mate: int, band: str
) -> None:
    board = chess.Board()
    manager = EnginePoolManager(StockfishConfig())
    analyze = AsyncMock(
        side_effect=[
            _witness(board.fen()),
            EngineAnalysis(raw_result=[{"pv": ["e7e5"], "score_mate": mate}]),
        ]
    )
    monkeypatch.setattr(manager, "analyze_hint", analyze)
    grade = (await grade_play_moves(board, [chess.Move.from_uci("e2e4")], manager))[0]
    assert grade.band == band
    assert grade.win_probability_loss == (0.5 if mate > 0 else 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fen", "uci", "score", "band", "termination"),
    [
        ("7k/8/5KQ1/8/8/8/8/8 w - - 0 1", "g6g7", 0, "sound", "CHECKMATE"),
        ("7k/5K2/8/6Q1/8/8/8/8 w - - 0 1", "g5g6", 600, "blunder", "STALEMATE"),
        ("7k/8/8/8/8/2r5/1K6/2B5 w - - 0 1", "b2c3", 600, "blunder", "INSUFFICIENT_MATERIAL"),
        ("7k/8/8/8/8/8/8/KR6 w - - 149 80", "b1b2", 600, "blunder", "SEVENTYFIVE_MOVES"),
    ],
)
async def test_terminal_proposals_use_exact_outcomes_but_still_compare_with_baseline(
    monkeypatch: pytest.MonkeyPatch, fen: str, uci: str, score: int, band: str, termination: str
) -> None:
    board = chess.Board(fen)
    manager = EnginePoolManager(StockfishConfig())
    analyze = AsyncMock(return_value=_witness(fen, score))
    monkeypatch.setattr(manager, "analyze_hint", analyze)
    move = chess.Move.from_uci(uci)
    grade = (await grade_play_moves(board, [move], manager))[0]
    branch = board.copy()
    branch.push(move)
    outcome = branch.outcome(claim_draw=False)
    assert outcome is not None and outcome.termination.name == termination
    assert grade.band == band
    if band == "blunder":
        assert grade.win_probability_loss > 0.30
    else:
        assert grade.win_probability_loss == 0
    analyze.assert_awaited_once_with(fen)


@pytest.mark.asyncio
@pytest.mark.parametrize(("cycles", "calls", "band"), [(1, 2, "sound"), (3, 1, "blunder")])
async def test_only_automatic_repetition_uses_replay_stack_without_engine_branch(
    monkeypatch: pytest.MonkeyPatch, cycles: int, calls: int, band: str
) -> None:
    board = chess.Board()
    for uci in ["g1f3", "g8f6", "f3g1", "f6g8"] * cycles + ["g1f3", "g8f6", "f3g1"]:
        board.push_uci(uci)
    move = chess.Move.from_uci("f6g8")
    branch = board.copy()
    branch.push(move)
    manager = EnginePoolManager(StockfishConfig())
    analyze = AsyncMock(side_effect=[_witness(board.fen(), 600), _witness(branch.fen(), -600)])
    monkeypatch.setattr(manager, "analyze_hint", analyze)
    grade = (await grade_play_moves(board, [move], manager))[0]
    assert grade.band == band
    assert analyze.await_count == calls
    assert branch.is_game_over(claim_draw=False) == (cycles == 3)
    assert not chess.Board(branch.fen()).is_game_over(claim_draw=False)


def test_multi_candidate_order_and_existing_hint_cache_budgets_are_shared_with_guard(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _register(db_session)
    play, game, _ = _source(db_session)
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    analyze = AsyncMock(side_effect=lambda fen, **_kwargs: _witness(fen))
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    response = _request(client, play, headers, moves=["d2d4", "e2e4", "g1f3"])
    assert response.status_code == 200, response.text
    assert [grade["san"] for grade in response.json()["grades"]] == ["d4", "e4", "Nf3"]
    assert analyze.await_count == 4
    for call in analyze.await_args_list:
        assert call.kwargs == {
            "nodes": HINT_NODES,
            "time_ms": HINT_TIME_MS,
            "multipv": HINT_MULTIPV,
        }
    repeated = _request(client, play, headers, moves=["e2e4"], mode="guard")
    assert repeated.status_code == 200, repeated.text
    assert analyze.await_count == 4
    assert repeated.json()["generation"] == response.json()["generation"] + 1
    assert repeated.json()["assistance_modes"] == ["candidates", "guard"]
    db_session.expire_all()
    stored_game = db_session.get(Game, game.id)
    assert stored_game is not None and stored_game.moves == []


@pytest.mark.asyncio
async def test_grading_does_not_queue_behind_hint_or_consume_reserved_opponent_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=2)
    entered, release = asyncio.Event(), asyncio.Event()
    searches = 0

    async def held_search(fen: str, **_kwargs: Any) -> EngineAnalysis:
        nonlocal searches
        searches += 1
        if searches == 1:
            entered.set()
            await release.wait()
        return _witness(fen)

    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    monkeypatch.setattr(
        PooledStockfishAdapter, "analyze_position", AsyncMock(side_effect=held_search)
    )
    board = chess.Board()
    first = asyncio.create_task(grade_play_moves(board, [chess.Move.from_uci("e2e4")], manager))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        with pytest.raises(TimeoutError, match="busy"):
            await grade_play_moves(board, [chess.Move.from_uci("d2d4")], manager)
        assert searches == 1
        # Acquiring the other slot succeeds while the grading baseline is held.
        async with asyncio.timeout(1), manager.interactive_pool.acquire():
            assert not first.done()
        release.set()
        grades = await asyncio.wait_for(first, timeout=1)
        assert grades[0].band == "sound"
        assert searches == 2
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await manager.close()
