from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.game_review import build_game_review
from scan64.api.profile_insights import _clocks, read_profile_insights
from scan64.chess.analysis.credit_models import PositiveMoveCredit
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, GameMoveReview
from scan64.chess.games.ingestion import ingest_fen, ingest_pgn
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import StudySession
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.tolerance import win_probability


def seed_game(
    session: Session, player_id: str, pgn: str, colour: str, losses: list[float | None]
) -> Game:
    game, _ = ingest_pgn(pgn)
    game.owner_player_id = player_id
    game.owner_color = colour  # type: ignore[assignment]
    session.add(game)
    board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
    owner_index = 0
    for ply, uci in enumerate(game.moves, 1):
        source = ingest_fen(board.fen(), game.id)
        mover = "white" if board.turn else "black"
        san = board.san(board.parse_uci(uci))
        board.push_uci(uci)
        reached = ingest_fen(board.fen(), game.id)
        raw: list[dict[str, object]] = [{"score_cp": 0, "pv": [uci]}]
        if board.is_checkmate():
            raw = [{"score_mate": 1, "pv": [uci]}]
        before = EngineAnalysis(position_id=source.id, raw_result=raw)
        after = EngineAnalysis(position_id=reached.id)
        probability = win_probability(raw[0])
        loss = None
        if mover == colour:
            loss = losses[owner_index]
            owner_index += 1
        session.add_all([source, reached, before, after])
        session.add(
            GameMoveReview(
                game_id=game.id,
                ply=ply,
                source_position_id=source.id,
                reached_position_id=reached.id,
                before_analysis_id=before.id,
                after_analysis_id=after.id,
                move_uci=uci,
                san=san,
                mover=mover,
                before_probability=probability if loss is not None else None,
                after_probability=(1 - probability + loss)
                if loss is not None and probability is not None
                else None,
            )
        )
    session.add(AnalysisJob(game_id=game.id, status="completed"))
    session.commit()
    return game


def seed_library(session: Session, player_id: str) -> dict[str, str]:
    """Disposable runtime library, also used by deterministic API regression tests."""
    games = [
        seed_game(
            session,
            player_id,
            '[TimeControl "300+0"]\n\n'
            "1. e4 {[%clk 0:00:30]} e5 2. Nf3 {[%clk 0:00:31]} Nc6 3. Bc4 Nf6 *",
            "white",
            [0.0, 0.1, None],
        ),
        seed_game(
            session,
            player_id,
            '[TimeControl "600"]\n\n1. d4 d5 {[%clk 0:00:00]} 2. c4 e6 {[%clk 0:01:00]} *',
            "black",
            [0.4, 0.2],
        ),
        seed_game(
            session,
            player_id,
            '[TimeControl "300"]\n\n1. f3 e5 2. g4 Qh4# 0-1',
            "black",
            [None, 0.0],
        ),
        seed_game(session, player_id, "1. e4 c6 *", "white", [None]),
    ]
    return {
        "italian": str(games[0].id),
        "queens_gambit": str(games[1].id),
        "imported_mate": str(games[2].id),
        "clockless_unscored": str(games[3].id),
    }


def test_pooled_owner_insights_and_imported_good_moment(
    client: TestClient,
    db_session: Session,
) -> None:
    registration = client.post("/v1/players", json={"id": "insight-owner"})
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    ids = seed_library(db_session, "insight-owner")
    seed_library(db_session, "different-owner")
    response = client.get("/v1/players/insight-owner/insights", headers=headers)
    assert response.status_code == 200
    report = response.json()
    assert report["overall"]["accuracy"] == pytest.approx(86)
    assert report["overall"]["scored_moves"] == 5
    assert report["overall"]["eligible_moves"] == 8
    assert [(row["scored_moves"], row["eligible_moves"]) for row in report["colours"]] == [
        (2, 4),
        (3, 4),
    ]
    assert report["phases"][1]["accuracy"] is None
    assert report["phases"][1]["eligible_moves"] == 0
    assert report["time_pressure"] == {
        "share": 50,
        "pressure_moves": 2,
        "valid_clock_moves": 4,
        "eligible_moves": 7,
    }
    openings = {row["label"]: row for row in report["openings"]}
    assert openings["Italian Game"]["accuracy"] == pytest.approx(95)
    assert openings["Queen's Gambit"]["accuracy"] == pytest.approx(70)
    assert openings["Caro-Kann Defense"]["accuracy"] is None
    assert client.get("/v1/players/different-owner/insights", headers=headers).status_code == 403
    game = db_session.get(Game, UUID(ids["imported_mate"]))
    assert game is not None
    review = build_game_review(game, db_session, include_key_moments=False)
    assert len(review.good_moments) == 1
    moment = review.good_moments[0]
    assert moment.ply == 4 and moment.san == "Qh4#"
    assert "No in-app hint" not in moment.explanation
    assert (
        review.moves[moment.ply - 1].fen
        == "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
    )
    assert not db_session.exec(select(PositiveMoveCredit)).all()
    assert not db_session.exec(select(ProfileObservation)).all()
    assert not db_session.exec(select(SkillState)).all()
    assert not db_session.exec(select(StudySession)).all()
    assert not db_session.new and not db_session.dirty


@pytest.mark.parametrize("control", ["?", "-", "1/86400", "1/259200", "0", "300+-1"])
def test_non_realtime_clocks_excluded(control: str, db_session: Session) -> None:
    game = seed_game(
        db_session,
        "owner",
        f'[TimeControl "{control}"]\n\n1. e4 {{[%clk 0:00:01]}} *',
        "white",
        [None],
    )
    assert _clocks(game) == (False, {})
    pressure = read_profile_insights("owner", db_session).time_pressure
    assert pressure.share is None and pressure.eligible_moves == 0


def test_mismatched_clock_mainline_is_not_used(db_session: Session) -> None:
    game = seed_game(
        db_session, "owner", '[TimeControl "300"]\n\n1. e4 {[%clk 0:00:01]} *', "white", [None]
    )
    game.pgn = '[TimeControl "300"]\n\n1. d4 {[%clk 0:00:01]} *'
    db_session.add(game)
    db_session.commit()
    pressure = read_profile_insights("owner", db_session).time_pressure
    assert pressure.share is None
    assert pressure.eligible_moves == 1 and pressure.valid_clock_moves == 0


@pytest.mark.parametrize(
    ("fen", "phase"),
    [
        ("8/7k/8/8/8/8/R7/K7 w - - 0 11", "Endgame"),
        (chess.STARTING_FEN.replace("0 1", "0 11"), "Middlegame"),
        ("8/7k/8/8/8/8/R7/K7 w - - 0 10", "Opening"),
    ],
)
def test_phase_uses_source_position(fen: str, phase: str, db_session: Session) -> None:
    move = "Ra3" if fen.startswith("8/") else "e4"
    seed_game(db_session, "owner", f'[SetUp "1"]\n[FEN "{fen}"]\n\n{move} *', "white", [0.0])
    figures = {figure.label: figure for figure in read_profile_insights("owner", db_session).phases}
    assert figures[phase].scored_moves == figures[phase].eligible_moves == 1
    assert figures[phase].accuracy == 100


def test_custom_start_is_not_classified_as_a_standard_opening(db_session: Session) -> None:
    seed_game(
        db_session,
        "owner",
        '[SetUp "1"]\n[FEN "7k/2p5/8/8/8/8/4P3/K7 w - - 0 11"]\n\n11. e4 c6 *',
        "white",
        [0.2],
    )
    openings = read_profile_insights("owner", db_session).openings
    assert [
        (figure.label, figure.accuracy, figure.scored_moves, figure.eligible_moves)
        for figure in openings
    ] == [("Unclassified opening", 80, 1, 1)]


def test_imported_good_moments_ignore_award_caps_and_rank_mates(db_session: Session) -> None:
    recording = json.loads(
        (Path(__file__).parents[1] / "fixtures/m61_credit_cap_game.json").read_text()
    )
    game = Game.model_validate(recording["game"])
    db_session.add_all(
        [
            game,
            *(Position.model_validate(row) for row in recording["positions"]),
            *(EngineAnalysis.model_validate(row) for row in recording["engine_analyses"]),
            *(GameMoveReview.model_validate(row) for row in recording["review_moves"]),
        ]
    )
    db_session.commit()
    # There is deliberately no PlaySession and no prospective award.
    review = build_game_review(game, db_session, include_key_moments=False)
    # Ply 13 is a mating capture, so the shared priority places it before ply 15.
    assert [moment.ply for moment in review.good_moments] == [11, 13, 15]
    assert not any(move.credit for move in review.moves)
    assert not db_session.exec(select(PositiveMoveCredit)).all()
    assert not db_session.new and not db_session.dirty
