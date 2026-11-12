from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
import scan64.learning.tolerance as policy
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.analysis.review import stage_game_review
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.registry import PluginRegistry
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.providers.stockfish.pool import PoolBoundAdapter


def _register(client: TestClient, name: str = "review-owner") -> dict[str, str]:
    player = client.post("/v1/players", json={"id": name})
    assert player.status_code == 200
    return {"Authorization": f"Bearer {player.json()['access_token']}"}


def _stage(session: Session, game: Game, evaluations: list[EngineAnalysis]) -> list[GameMoveReview]:
    session.add(game)
    positions = {}
    analyses = {}
    rows = stage_game_review(game, evaluations, positions, analyses, set())
    session.add_all(positions.values())
    session.add_all(analyses.values())
    session.add_all(rows)
    session.commit()
    return rows


@pytest.mark.parametrize(
    ("loss", "band"),
    [
        (None, "unscored"),
        (0.0, "sound"),
        (0.08, "sound"),
        (0.0800001, "inaccuracy"),
        (0.15, "inaccuracy"),
        (0.1500001, "mistake"),
        (0.30, "mistake"),
        (0.3000001, "blunder"),
    ],
)
def test_scan64_policy_boundaries(loss: float | None, band: str) -> None:
    assert policy.review_band(loss) == band
    assert policy.review_loss(None, 0.8) is None
    assert policy.review_loss(0.8, None) is None
    assert policy.review_loss(0.2, 0.1) == 0.0
    assert policy.review_loss(1.0, 1.0) == 1.0


@pytest.mark.parametrize(
    ("fen", "terminal", "probability"),
    [
        ("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3", "checkmate", 0.0),
        ("7k/5K2/6Q1/8/8/8/8/8 b - - 0 1", "stalemate", 0.5),
        ("7k/8/5K2/8/8/8/8/8 w - - 0 1", "insufficient_material", 0.5),
        ("7k/8/8/8/8/8/R7/K7 w - - 150 90", "seventyfive_moves", 0.5),
    ],
)
def test_exact_automatic_terminal_outcomes(fen: str, terminal: str, probability: float) -> None:
    board = chess.Board(fen)
    assert board.is_valid()
    assert policy.review_probability(board, {"score_cp": 500}) == (probability, terminal)


def test_fivefold_uses_full_stack_and_claimable_draws_keep_engine_score() -> None:
    board = chess.Board()
    cycle = ["g1f3", "g8f6", "f3g1", "f6g8"]
    for uci in cycle * 2:
        board.push_uci(uci)
    assert board.can_claim_threefold_repetition()
    expected = policy.win_probability({"score_cp": 300})
    assert policy.review_probability(board, {"score_cp": 300}) == (expected, None)
    for uci in cycle * 2:
        board.push_uci(uci)
    assert policy.review_probability(board, {"score_cp": 300}) == (0.5, "fivefold_repetition")
    assert policy.review_probability(chess.Board(board.fen()), {"score_cp": 300}) == (
        expected,
        None,
    )
    fifty = chess.Board("7k/8/8/8/8/8/R7/K7 w - - 100 90")
    assert fifty.can_claim_fifty_moves()
    assert policy.review_probability(fifty, {"score_cp": 300}) == (expected, None)


@pytest.mark.parametrize("owner", ["white", "black", None])
def test_recorded_scores_owner_accuracy_graph_and_engine_free_get(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, owner: str | None
) -> None:
    auth = _register(client)
    recorded = json.loads((Path(__file__).parents[1] / "fixtures/m59_fast_pass.json").read_text())
    board = chess.Board()
    moves = []
    for san in recorded["moves"]:
        move = board.parse_san(san)
        moves.append(move.uci())
        board.push(move)
    game = Game(
        pgn="", moves=moves, result="0-1", owner_color=owner, owner_player_id="review-owner"
    )
    analyses = [EngineAnalysis(**data) for data in recorded["evaluations"]]
    rows = _stage(db_session, game, analyses)
    # A newer focused search must not replace the recorded accuracy inputs.
    db_session.add(
        EngineAnalysis(
            position_id=rows[0].source_position_id,
            raw_result=[{"score_cp": -9999}],
            config={"nodes": 1000000},
        )
    )
    db_session.commit()

    async def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("GET started an engine search")

    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", forbidden)
    response = client.get(f"/v1/games/{game.id}/review", headers=auth)
    assert response.status_code == 200
    review = response.json()
    assert review["owner_color"] == owner
    assert not review["backfill_required"]
    assert len(review["moves"]) == len(moves)
    assert review["moves"][-1]["fen"] == board.fen()
    losses = []
    for index, move in enumerate(review["moves"]):
        before = policy.win_probability(analyses[index].raw_result[0])
        after = policy.win_probability(analyses[index + 1].raw_result[0])
        assert move["loss"] == pytest.approx(policy.review_loss(before, after))
        assert after is not None
        assert move["white_probability"] == pytest.approx(1 - after if index % 2 == 0 else after)
        if move["mover"] == owner:
            losses.append(move["loss"])
            assert move["band"] == policy.review_band(move["loss"])
        else:
            assert move["band"] is None
    assert review["total_owner_moves"] == len(losses)
    assert sum(review["band_counts"].values()) == len(losses)
    if losses:
        assert review["accuracy"] == pytest.approx(100 * (1 - sum(losses) / len(losses)))
        monkeypatch.setattr(policy, "SOUND_MOVE_WIN_PROBABILITY_LOSS", 1.0)
        changed = client.get(f"/v1/games/{game.id}/review", headers=auth).json()
        assert changed["band_counts"]["sound"] == len(losses)
    else:
        assert review["accuracy"] is None
    foreign = _register(client, "foreign")
    for method, path in [("get", "review"), ("post", "review/backfill")]:
        assert (
            getattr(client, method)(f"/v1/games/{game.id}/{path}", headers=foreign).status_code
            == 404
        )


def test_missing_scores_and_custom_black_fen_are_explicit(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    board = chess.Board()
    board.push_uci("e2e4")
    game = Game(
        pgn="",
        moves=["e7e5", "g1f3"],
        headers={"FEN": board.fen()},
        owner_color="black",
        owner_player_id="review-owner",
    )
    _stage(db_session, game, [EngineAnalysis(raw_result=[]) for _ in range(3)])
    review = client.get(f"/v1/games/{game.id}/review", headers=auth).json()
    assert review["moves"][0]["is_owner_move"]
    assert review["moves"][0]["band"] == "unscored"
    assert review["moves"][0]["source_fen"] == board.fen()
    assert review["accuracy"] is None
    assert review["scored_moves"] == 0
    assert review["total_owner_moves"] == 1
    assert review["band_counts"]["unscored"] == 1


@pytest.mark.asyncio
async def test_full_pass_retains_every_score_without_second_search_and_repeated_ply_identity(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    async def analyze(_self: object, fen: str, **limits: Any) -> EngineAnalysis:
        calls.append((fen, limits))
        return EngineAnalysis(raw_result=[{"score_cp": 0}], config=limits)

    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", analyze)
    moves = ["g1f3", "g8f6", "f3g1", "f6g8"] * 4
    game = Game(pgn="", moves=moves, owner_color="white", owner_player_id="review-owner")
    db_session.add(game)
    db_session.commit()
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    await jobs.run_analysis_for_game(game, db_session, registry=registry)
    rows = db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == game.id).order_by(GameMoveReview.ply)
    ).all()
    assert len(calls) == len(moves) + 1
    assert all(limits["nodes"] == 10000 for _, limits in calls)
    assert len(rows) == len(moves)
    assert rows[-1].after_probability == 0.5
    assert rows[-1].after_terminal == "fivefold_repetition"
    assert len({row.id for row in rows}) == len(moves)
    for row in rows:
        analysis = db_session.get(EngineAnalysis, row.before_analysis_id)
        assert analysis is not None and analysis.position_id == row.source_position_id


def test_completed_review_only_backfill_preserves_learning_and_full_analysis_guard(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _register(client)
    game = Game(pgn="", moves=["e2e4", "e7e5"], owner_color="white", owner_player_id="review-owner")
    db_session.add(game)
    db_session.add(AnalysisJob(game_id=game.id, status="completed"))
    db_session.add(SkillState(player_id="review-owner", concept_code="test", alpha=3.5))
    db_session.add(
        ProfileObservation(
            player_id="review-owner",
            game_id=str(game.id),
            position_id="historical",
            skill_id="test",
            observed_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    calls = []

    async def analyze(_self: object, fen: str, **limits: Any) -> EngineAnalysis:
        calls.append(fen)
        return EngineAnalysis(raw_result=[{"score_cp": 0}], config=limits)

    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", analyze)
    monkeypatch.setattr(PoolBoundAdapter, "analyze_position", analyze)

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("Backfill entered focused/learning analysis")

    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", forbidden)
    models = [
        PersistedLessonOpportunity,
        Evidence,
        ProfileObservation,
        SkillState,
        StudySession,
        LessonAttempt,
    ]
    before = {
        model.__name__: [
            row.model_dump(mode="json") for row in db_session.exec(select(model)).all()
        ]
        for model in models
    }
    assert client.get(f"/v1/games/{game.id}/review", headers=auth).json()["backfill_required"]
    response = client.post(f"/v1/games/{game.id}/review/backfill", headers=auth)
    assert response.status_code == 200
    assert not response.json()["backfill_required"]
    assert len(calls) == 3
    assert client.post(f"/v1/games/{game.id}/review/backfill", headers=auth).status_code == 200
    assert len(calls) == 3
    after = {
        model.__name__: [
            row.model_dump(mode="json") for row in db_session.exec(select(model)).all()
        ]
        for model in models
    }
    assert before == after
    assert len(db_session.exec(select(GameMoveReview)).all()) == 2
    rerun = AnalysisJob(game_id=game.id)
    db_session.add(rerun)
    db_session.commit()
    jobs.execute_analysis_job(rerun.id)
    db_session.refresh(rerun)
    assert rerun.status == "completed"
    assert len(calls) == 3


def test_backfill_requires_completed_game(client: TestClient, db_session: Session) -> None:
    auth = _register(client)
    game = Game(pgn="", moves=["e2e4"], owner_color="white", owner_player_id="review-owner")
    db_session.add(game)
    db_session.commit()
    assert client.post(f"/v1/games/{game.id}/review/backfill", headers=auth).status_code == 409


def _key_lesson(session: Session, game: Game, row: GameMoveReview) -> PersistedLessonOpportunity:
    position = session.get(Position, row.source_position_id)
    assert position is not None
    board = chess.Board(position.fen)
    move = chess.Move.from_uci("e2e4")
    if move not in board.legal_moves:
        move = next(iter(board.legal_moves))
    accepted = board.san(move)
    evidence = Evidence(
        evidence_id=f"key-proof-{row.ply}",
        kind="missed_opportunity",
        position_id=str(row.source_position_id),
        engine_analysis_id=str(row.before_analysis_id),
        claim="Trusted key-moment fixture",
        payload={"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION},
    )
    analysis = EngineAnalysis(
        position_id=row.source_position_id,
        raw_result=[{"pv": [move.uci()], "score_cp": 200}],
        config={"nodes": 100000},
    )
    spec = {
        "schema_version": "1.0",
        "lesson_id": "generated-key",
        "source": {"kind": "custom", "fen": position.fen},
        "diagnosis": {
            "primary": "threat_processing.missed_capture",
            "confidence": 1.0,
            "evidence_refs": [evidence.evidence_id],
        },
        "objective": {"type": "find_best_move", "instruction": "Play e4."},
        "interaction": {
            "input": "click",
            "maximum_attempts": 3,
            "accepted_moves": [{"san": accepted}],
        },
        "verification": {"status": "verified", "engine": "test"},
        "hints": [],
        "explanation": {"text": "Develop the centre."},
    }
    lesson = PersistedLessonOpportunity(
        game_id=game.id,
        source_position_id=row.source_position_id,
        player_id=game.owner_player_id,
        lesson_spec=spec,
        verification_analysis_id=analysis.id,
    )
    session.add_all([evidence, analysis, lesson])
    session.add(
        ReviewSchedule(
            player_id=game.owner_player_id,
            item_id=str(lesson.id),
            skill_id="threat_processing.missed_capture",
            next_review_at=datetime.now(UTC),
        )
    )
    session.commit()
    return lesson


@pytest.mark.parametrize("used", [0, 2, 3])
def test_key_moment_reverifies_stored_analysis_and_shares_daily_lifetime_attempts(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, used: int
) -> None:
    auth = _register(client)
    game = Game(pgn="", moves=["d2d4"], owner_color="white", owner_player_id="review-owner")
    rows = _stage(
        db_session,
        game,
        [
            EngineAnalysis(raw_result=[{"score_cp": 200}]),
            EngineAnalysis(raw_result=[{"score_cp": 200}]),
        ],
    )
    lesson = _key_lesson(db_session, game, rows[0])
    daily = StudySession(player_id="review-owner", domain="daily_training")
    db_session.add(daily)
    for _ in range(used):
        db_session.add(
            LessonAttempt(
                session_id=daily.id,
                player_id="review-owner",
                lesson_id=str(lesson.id),
                opportunity_id=lesson.id,
                source_kind="persisted_opportunity",
                elapsed_ms=100,
                hints_used=0,
                grading_status="graded",
                profile_update_result="applied",
                success=False,
            )
        )
    db_session.commit()

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("GET must never search")

    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", forbidden)
    review = client.get(f"/v1/games/{game.id}/review", headers=auth).json()
    assert len(review["key_moments"]) == 1
    moment = review["key_moments"][0]
    assert moment["attempts_used"] == used
    assert moment["attempts_remaining"] == 3 - used
    assert moment["lesson"]["hints"]
    assert moment["lesson"]["diagnosis"]["secondary"] == []
    db_session.refresh(lesson)
    assert lesson.verification_status == "verified"
    attempt = client.post(
        "/v1/learning/lesson-attempts",
        headers=auth,
        json={
            "session_id": review["session_id"],
            "lesson_id": str(lesson.id),
            "source_kind": "persisted_opportunity",
            "submitted_move": "e2e4",
            "hints_used": 1,
            "elapsed_ms": 100,
        },
    )
    if used == 3:
        assert attempt.status_code == 409
    else:
        assert attempt.status_code == 200
        assert attempt.json()["success"]
        state = db_session.get(SkillState, ("review-owner", "threat_processing.missed_capture"))
        assert state is not None
        assert state.alpha == pytest.approx(state.prior_alpha + 0.5)
        assert state.beta == pytest.approx(state.prior_beta)
    lesson.verification_status = "invalid"
    db_session.add(lesson)
    db_session.commit()
    assert client.get(f"/v1/games/{game.id}/review", headers=auth).json()["key_moments"] == []


def test_key_moment_requires_stored_analysis_reference(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    game = Game(pgn="", moves=["d2d4"], owner_color="white", owner_player_id="review-owner")
    rows = _stage(
        db_session,
        game,
        [
            EngineAnalysis(raw_result=[{"score_cp": 200}]),
            EngineAnalysis(raw_result=[{"score_cp": 200}]),
        ],
    )
    lesson = _key_lesson(db_session, game, rows[0])
    lesson.verification_analysis_id = None
    db_session.add(lesson)
    db_session.commit()
    assert client.get(f"/v1/games/{game.id}/review", headers=auth).json()["key_moments"] == []


def test_key_moments_rank_loss_then_ply_cap_three_and_persist_failed_reverification(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    game = Game(
        pgn="",
        moves=["d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6", "c1g5", "f8e7"],
        owner_color="white",
        owner_player_id="review-owner",
    )
    scores = [100, 100, 200, 200, 200, 200, 50, 50, 0]
    rows = _stage(
        db_session, game, [EngineAnalysis(raw_result=[{"score_cp": score}]) for score in scores]
    )
    lessons = [_key_lesson(db_session, game, row) for row in rows[::2]]
    review = client.get(f"/v1/games/{game.id}/review", headers=auth).json()
    assert [moment["ply"] for moment in review["key_moments"]] == [3, 5, 1]
    # Editing a stored answer cannot bypass a fresh, engine-free verification.
    invalid = lessons[1]
    payload = dict(invalid.lesson_spec)
    payload["interaction"] = {**payload["interaction"], "accepted_moves": [{"san": "Qa8"}]}
    invalid.lesson_spec = payload
    db_session.add(invalid)
    db_session.commit()
    review = client.get(f"/v1/games/{game.id}/review", headers=auth).json()
    assert [moment["ply"] for moment in review["key_moments"]] == [5, 1, 7]
    db_session.refresh(invalid)
    assert invalid.verification_status == "invalid"
