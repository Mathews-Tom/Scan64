from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import chess
import pytest
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.api.game_review import build_game_review
from scan64.chess.analysis.credit import (
    BEFORE_END,
    CAPTURE_SKILL,
    apply_positive_move_credits,
    project_move_credits,
)
from scan64.chess.analysis.credit_models import PositiveMoveCredit
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview, PersistedLessonOpportunity
from scan64.chess.analysis.orchestration import FastPassResult
from scan64.chess.games.assistance import SessionHintReveal
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.plugins.registry import PluginRegistry
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.tolerance import win_probability

FIXTURES = Path(__file__).parents[1] / "fixtures"
RECORDING = json.loads((FIXTURES / "m61_positive_proofs.json").read_text())
CORPUS = json.loads((FIXTURES / "m48/recorded.json").read_text())


def _stage(
    session: Session, name: str = "hung_white"
) -> tuple[Game, PlaySession, GameMoveReview, Position]:
    case = RECORDING["cases"][name]
    recorded_game = next(game for game in CORPUS["games"] if game["id"] == case["source_game"])
    board = chess.Board()
    moves = []
    for san in recorded_game["moves"][:case["source_ply"] - 1]:
        move = board.parse_san(san)
        moves.append(move.uci())
        board.push(move)
    assert board.fen() == case["fen"]
    objective = board.parse_uci(case["raw_result"][0]["pv"][0])
    game = Game(
        pgn="", owner_player_id="credit-owner", owner_color="white" if board.turn else "black",
        moves=[*moves, objective.uci()], result="1-0",
    )
    play = PlaySession(
        player_id="credit-owner", game_id=game.id, status="completed",
        assistance_tracking_version=1,
        opponent_config={"initial_ply": "0", "custom_start": "false"},
    )
    source = ingest_fen(board.fen(), game.id)
    predecessor = ingest_fen(case["predecessor_fen"], game.id)
    before = EngineAnalysis(position_id=source.id, raw_result=copy.deepcopy(case["raw_result"]))
    prior_analysis = EngineAnalysis(position_id=predecessor.id)
    probability = win_probability(before.raw_result[0])
    assert probability is not None
    san = board.san(objective)
    board.push(objective)
    reached = ingest_fen(board.fen(), game.id)
    after = EngineAnalysis(position_id=reached.id)
    # A8 probabilities are controlled here to isolate persistence, not claim
    # another recorded engine search of the resulting position.
    row = GameMoveReview(
        game_id=game.id, ply=case["source_ply"], source_position_id=source.id,
        reached_position_id=reached.id, before_analysis_id=before.id,
        after_analysis_id=after.id, move_uci=objective.uci(), san=san,
        mover=game.owner_color, before_probability=probability, after_probability=1 - probability,
    )
    previous_board = chess.Board(predecessor.fen)
    previous_move = previous_board.parse_uci(case["preceding_move_uci"])
    previous = GameMoveReview(
        game_id=game.id, ply=row.ply - 1, source_position_id=predecessor.id,
        reached_position_id=source.id, before_analysis_id=prior_analysis.id,
        after_analysis_id=before.id, move_uci=previous_move.uci(),
        san=previous_board.san(previous_move), mover="white" if previous_board.turn else "black",
    )
    session.add_all([game, play, source, predecessor, reached, before, after, prior_analysis,
                     row, previous])
    session.commit()
    return game, play, row, source


def _credits(session: Session) -> list[PositiveMoveCredit]:
    return list(session.exec(select(PositiveMoveCredit)).all())


@pytest.mark.parametrize("name", ["hung_white", "hung_black", "material_check", "mate_check"])
def test_credit_and_alpha_commit_once_per_source_and_skill(db_session: Session, name: str) -> None:
    game, play, row, source = _stage(db_session, name)
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    ledger = _credits(db_session)
    assert len(ledger) == 1
    credit = ledger[0]
    assert (credit.game_id, credit.canonical_id, credit.ply) == (
        game.id, source.canonical_id, row.ply,
    )
    skill = db_session.get(SkillState, (play.player_id, credit.skill_id))
    assert skill is not None and skill.alpha == skill.prior_alpha + 1
    assert skill.beta == skill.prior_beta
    original_alpha = skill.alpha
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    # Even losing the analysis marker cannot bypass the composite ledger key.
    play.credit_analysis_status = None
    db_session.add(play)
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    db_session.refresh(skill)
    assert len(_credits(db_session)) == 1
    assert skill.alpha == original_alpha
    assert play.credit_analysis_status == "completed"


def test_credit_and_mastery_roll_back_with_full_analysis_transaction(db_session: Session) -> None:
    game, play, _, _ = _stage(db_session)
    apply_positive_move_credits(game, db_session, rating=1500)
    assert len(_credits(db_session)) == 1
    db_session.rollback()
    db_session.refresh(play)
    assert play.credit_analysis_status is None
    assert _credits(db_session) == []
    assert db_session.exec(select(SkillState)).all() == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"assistance_tracking_version": None}, "unknown assistance (session was not tracked)"),
        ({"coach_mode": True}, "coach mode"),
        ({"opponent_config": {"custom_start": "true", "initial_ply": "0"}},
         "started from a studied position"),
        ({"status": "active"}, BEFORE_END),
    ],
)
def test_ineligible_session_does_not_award(
    db_session: Session, change: dict[str, object], reason: str
) -> None:
    game, play, row, _ = _stage(db_session)
    for key, value in change.items():
        setattr(play, key, value)
    db_session.add(play)
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    assert _credits(db_session) == []
    assert project_move_credits(game, db_session)[row.ply] == (None, reason)


def test_analysis_before_game_end_is_permanently_ineligible(db_session: Session) -> None:
    game, play, row, _ = _stage(db_session)
    game.result = "*"
    db_session.add(game)
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    assert play.credit_analysis_status == BEFORE_END
    game.result = "1-0"
    db_session.add(game)
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    assert _credits(db_session) == []
    assert project_move_credits(game, db_session)[row.ply] == (None, BEFORE_END)


def test_imported_game_and_prefix_moves_are_not_positive_evidence(db_session: Session) -> None:
    game, play, row, _ = _stage(db_session)
    play.opponent_config = {"initial_ply": str(row.ply)}
    db_session.add(play)
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    assert _credits(db_session) == []
    assert "history" in str(project_move_credits(game, db_session)[row.ply][1])
    db_session.delete(play)
    db_session.commit()
    assert "unknown assistance" in str(project_move_credits(game, db_session)[row.ply][1])


def test_hint_on_canonical_revisit_excludes_despite_different_fen_counters(
    db_session: Session,
) -> None:
    game, play, row, source = _stage(db_session)
    hinted_board = chess.Board(source.fen)
    hinted_board.halfmove_clock += 8
    hinted_board.fullmove_number += 4
    assert hinted_board.fen() != source.fen
    db_session.add(SessionHintReveal(
        session_id=play.id, player_id=play.player_id, prefix_hash="earlier-takeback-prefix",
        canonical_id=hinted_board.epd(), ply=row.ply + 8, initial_fen=chess.STARTING_FEN,
        moves=["e2e4"], highest_step=1,
    ))
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    assert _credits(db_session) == []
    assert project_move_credits(game, db_session)[row.ply] == (
        None, "in-app hint revealed at this position"
    )


@pytest.mark.parametrize("persisted", [False, True])
def test_staged_and_stored_failures_map_reached_position_to_its_source(
    db_session: Session, persisted: bool
) -> None:
    game, play, row, _ = _stage(db_session)
    observations = [(row.reached_position_id, CAPTURE_SKILL)]
    if persisted:
        db_session.add(ProfileObservation(
            player_id=play.player_id, game_id=str(game.id),
            position_id=str(row.reached_position_id),
            skill_id=CAPTURE_SKILL, observed_at=datetime.now(UTC),
        ))
        db_session.commit()
        observations = []
    apply_positive_move_credits(game, db_session, rating=1500, staged_observations=observations)
    db_session.commit()
    assert _credits(db_session) == []


@pytest.mark.parametrize("attempted_legacy", [False, True])
def test_same_source_lesson_or_attempt_excludes_credit(
    db_session: Session, attempted_legacy: bool
) -> None:
    game, play, _, source = _stage(db_session)
    lesson = PersistedLessonOpportunity(
        game_id=game.id, source_position_id=source.id,
        player_id=None if attempted_legacy else play.player_id,
        lesson_spec={"diagnosis": {"primary": CAPTURE_SKILL}},
    )
    db_session.add(lesson)
    if attempted_legacy:
        study = StudySession(player_id=play.player_id, domain="game_analysis")
        db_session.add(study)
        db_session.flush()
        db_session.add(LessonAttempt(
            session_id=study.id, player_id=play.player_id, lesson_id=str(lesson.id),
            source_kind="game_analysis", opportunity_id=lesson.id, elapsed_ms=1, hints_used=0,
            grading_status="accepted", profile_update_result="applied", success=True,
        ))
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    assert _credits(db_session) == []


def test_other_source_lesson_does_not_suppress_a_legitimate_success(db_session: Session) -> None:
    game, play, _, _ = _stage(db_session)
    other = ingest_fen(chess.STARTING_FEN, game.id)
    db_session.add(other)
    db_session.add(PersistedLessonOpportunity(
        game_id=game.id, source_position_id=other.id, player_id=play.player_id,
        lesson_spec={"diagnosis": {"primary": CAPTURE_SKILL}},
    ))
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    assert len(_credits(db_session)) == 1


def test_retired_skill_never_receives_positive_credit(db_session: Session) -> None:
    game, play, row, _ = _stage(db_session)
    retired = SkillState(
        player_id=play.player_id, concept_code=CAPTURE_SKILL, retired_at=datetime.now(UTC)
    )
    db_session.add(retired)
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    assert _credits(db_session) == []
    assert retired.alpha == 1
    assert project_move_credits(game, db_session)[row.ply] == (None, "retired skill")


def test_repeated_engine_free_review_reads_never_award(db_session: Session) -> None:
    game, _, row, _ = _stage(db_session)
    for _ in range(2):
        review = build_game_review(game, db_session, include_key_moments=False)
        selected = next(move for move in review.moves if move.ply == row.ply)
        assert selected.credit is None
        assert selected.credit_exclusion == "not credited by full analysis"
    assert _credits(db_session) == []
    assert db_session.exec(select(SkillState)).all() == []
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    skill = db_session.get(SkillState, (game.owner_player_id, CAPTURE_SKILL))
    assert skill is not None
    alpha = skill.alpha
    for _ in range(2):
        review = build_game_review(game, db_session, include_key_moments=False)
        selected = next(move for move in review.moves if move.ply == row.ply)
        assert selected.credit is not None
        assert selected.credit["skill_id"] == CAPTURE_SKILL
        assert selected.credit["explanation"]
        assert selected.credit["credited"] is True
    assert len(_credits(db_session)) == 1
    assert skill.alpha == alpha


@pytest.mark.asyncio
async def test_review_backfill_does_not_award_or_change_the_analysis_marker(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    game, play, _, _ = _stage(db_session)

    async def recorded_pass(*args: object, **kwargs: object) -> FastPassResult:
        return FastPassResult(candidates=[], evaluations=[
            EngineAnalysis(position_id=uuid4(), raw_result=[{"score_cp": 0, "pv": []}])
            for _ in range(len(game.moves) + 1)
        ])

    monkeypatch.setattr(jobs.FastPassOrchestrator, "run_fast_pass", recorded_pass)
    await jobs.backfill_game_review(game, db_session)
    assert _credits(db_session) == []
    assert db_session.exec(select(SkillState)).all() == []
    assert play.credit_analysis_status is None


@pytest.mark.asyncio
async def test_full_analysis_integrates_positive_credit_in_its_transaction(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    game, _, row, _ = _stage(db_session)
    case = RECORDING["cases"]["hung_white"]

    async def recorded_pass(*args: object, **kwargs: object) -> FastPassResult:
        evaluations = [EngineAnalysis(position_id=uuid4(), raw_result=[{"score_cp": 0, "pv": []}])
                       for _ in range(len(game.moves) + 1)]
        evaluations[row.ply - 1].raw_result = copy.deepcopy(case["raw_result"])
        return FastPassResult(candidates=[], evaluations=evaluations)

    monkeypatch.setattr(jobs.FastPassOrchestrator, "run_fast_pass", recorded_pass)
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    await jobs.run_analysis_for_game(game, db_session, registry=registry)
    assert len(_credits(db_session)) == 1


def test_repeated_legal_source_with_different_counters_earns_one_credit(
    db_session: Session,
) -> None:
    game, play, first, source = _stage(db_session, "mate_check")
    board = chess.Board(source.fen)
    # Qf6+ Kg8 Qd6 Kh8 genuinely revisits the source without captures.
    cycle = ["d6f6", "h8g8", "f6d6", "g8h8"]
    for uci in cycle:
        board.push_uci(uci)
    repeated = ingest_fen(board.fen(), game.id)
    assert repeated.fen != source.fen
    assert repeated.canonical_id == source.canonical_id
    before = EngineAnalysis(
        position_id=repeated.id,
        raw_result=copy.deepcopy(RECORDING["cases"]["mate_check"]["raw_result"]),
    )
    objective = board.parse_uci(first.move_uci)
    san = board.san(objective)
    board.push(objective)
    reached = ingest_fen(board.fen(), game.id)
    after = EngineAnalysis(position_id=reached.id)
    second = GameMoveReview(
        game_id=game.id, ply=first.ply + 4, source_position_id=repeated.id,
        reached_position_id=reached.id, before_analysis_id=before.id,
        after_analysis_id=after.id, move_uci=objective.uci(), san=san,
        mover=first.mover, before_probability=1.0, after_probability=0.0,
    )
    game.moves = [*game.moves, *cycle[1:], first.move_uci]
    db_session.add_all([game, repeated, before, reached, after, second])
    db_session.commit()
    apply_positive_move_credits(game, db_session, rating=1500)
    db_session.commit()
    credits = _credits(db_session)
    assert len(credits) == 1
    assert credits[0].ply == first.ply
    skill = db_session.get(SkillState, (play.player_id, credits[0].skill_id))
    assert skill is not None and skill.alpha == skill.prior_alpha + 1
    assert project_move_credits(game, db_session)[second.ply] == (
        None, f"same position and skill already credited at ply {first.ply}"
    )
