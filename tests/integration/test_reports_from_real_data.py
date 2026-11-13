from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

import scan64.api.game_review as game_review
import scan64.api.learning as learning
import scan64.api.reports as reports
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.providers.stockfish.adapter import StockfishAdapter
from scan64.providers.stockfish.pool import PoolBoundAdapter


def create_source_position(db_session: Session, game_id: UUID) -> Position:
    position = Position(
        game_id=game_id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial",
    )
    db_session.add(position)
    db_session.flush()
    return position


def create_report_game(
    db_session: Session,
    player_id: str,
    *,
    owner_color: Literal["white", "black"] = "white",
) -> Game:
    game = Game(
        pgn='[Result "*"]\n\n1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 *',
        moves=["g1f3", "g8f6", "f3g1", "f6g8"] * 2,
        owner_player_id=player_id,
        owner_color=owner_color,
        result="*",
    )
    db_session.add(game)
    db_session.flush()
    db_session.add(AnalysisJob(game_id=game.id, status="completed", completed_at=datetime.now(UTC)))
    return game


def create_report_lesson(
    db_session: Session,
    game: Game,
    diagnosis: str,
    evidence_ref: str | None = None,
    *,
    marked: bool = True,
    ply: int = 1,
    with_review: bool = False,
    before_probability: float | None = 0.75,
    after_probability: float | None = 0.75,
    source: Position | None = None,
) -> PersistedLessonOpportunity:
    board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
    for uci in game.moves[: ply - 1]:
        board.push_uci(uci)
    move = chess.Move.from_uci(game.moves[ply - 1])
    san = board.san(move)
    if source is None:
        source = Position(
            game_id=game.id,
            fen=board.fen(),
            side_to_move="w" if board.turn else "b",
            canonical_id=board.epd(),
        )
        db_session.add(source)
        db_session.flush()
    verification = EngineAnalysis(
        position_id=source.id,
        raw_result=[{"pv": [move.uci()], "score_cp": 100}],
    )
    db_session.add(verification)
    db_session.flush()
    evidence_ref = evidence_ref or f"report-proof-{verification.id}"
    db_session.add(
        Evidence(
            evidence_id=evidence_ref,
            kind="missed_opportunity",
            position_id=str(source.id),
            engine_analysis_id="report-analysis",
            claim="Report-layer diagnosis fixture",
            payload={"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION} if marked else {},
        )
    )
    opportunity = PersistedLessonOpportunity(
        game_id=game.id,
        player_id=game.owner_player_id,
        source_position_id=source.id,
        verification_status="verified",
        verification_analysis_id=verification.id,
        lesson_spec={
            "schema_version": "0.2.0",
            "lesson_id": str(source.id),
            "source": {"kind": "custom", "fen": source.fen},
            "diagnosis": {"primary": diagnosis, "confidence": 1.0, "evidence_refs": [evidence_ref]},
            "objective": {"type": "find_best_move", "instruction": f"Play {san}."},
            "interaction": {
                "input": "click",
                "maximum_attempts": 3,
                "accepted_moves": [{"san": san}],
            },
            "verification": {"status": "verified", "engine": "test"},
            "hints": [],
            "explanation": {"text": "Develop the centre."},
        },
    )
    db_session.add(opportunity)
    db_session.flush()
    db_session.add(
        ReviewSchedule(
            player_id=str(game.owner_player_id),
            item_id=str(opportunity.id),
            next_review_at=datetime.now(UTC),
            skill_id=diagnosis,
        )
    )
    if with_review:
        before = EngineAnalysis(position_id=source.id, raw_result=verification.raw_result)
        board.push(move)
        reached = Position(
            game_id=game.id,
            fen=board.fen(),
            side_to_move="w" if board.turn else "b",
            canonical_id=board.epd(),
        )
        db_session.add(reached)
        db_session.flush()
        after = EngineAnalysis(position_id=reached.id, raw_result=[{"score_cp": -100}])
        db_session.add_all([before, after])
        db_session.flush()
        db_session.add(
            GameMoveReview(
                game_id=game.id,
                ply=ply,
                source_position_id=source.id,
                reached_position_id=reached.id,
                before_analysis_id=before.id,
                after_analysis_id=after.id,
                move_uci=move.uci(),
                san=san,
                mover="white" if source.side_to_move == "w" else "black",
                before_probability=before_probability,
                after_probability=after_probability,
            )
        )
    return opportunity


def create_player_token(client: TestClient, player_id: str) -> str:
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": player_id, "preferences": {}},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


def test_patterns_reports_recurring_diagnosis_from_owned_games(
    client: TestClient, db_session: Session
) -> None:
    player_id = "pattern-player"
    token = create_player_token(client, player_id)
    evidence_refs = ["evidence-1", "evidence-2", "evidence-3"]
    game_ids: list[str] = []
    for evidence_ref in evidence_refs:
        game = create_report_game(db_session, player_id)
        game_ids.append(str(game.id))
        create_report_lesson(db_session, game, "threat_processing.missed_capture", evidence_ref)
    other_game = Game(pgn="1. d4 d5", owner_player_id="another-player")
    db_session.add(other_game)
    db_session.flush()
    db_session.add(
        PersistedLessonOpportunity(
            game_id=other_game.id,
            source_position_id=create_source_position(db_session, other_game.id).id,
            lesson_spec={
                "diagnosis": {
                    "primary": "tactics.fork.knight",
                    "evidence_refs": ["foreign-evidence"],
                }
            },
        )
    )
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/patterns",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    report = response.json()
    assert report["player_id"] == player_id
    assert report["minimum_occurrences"] == 3
    assert report["eligible_game_count"] == 3
    assert report["status"] == "recurring_diagnosis"
    assert len(report["recurring_diagnoses"]) == 1
    pattern = report["recurring_diagnoses"][0]
    assert pattern["diagnosis"] == "threat_processing.missed_capture"
    assert pattern["name"] == "Missed capture"
    assert pattern["occurrence_count"] == 3
    assert pattern["game_ids"] == sorted(game_ids)
    assert pattern["evidence_references"] == evidence_refs
    assert pattern["total_occurrences"] == 3
    assert pattern["scored_occurrences"] == 0
    assert pattern["observed_loss"] is None
    assert pattern["template"]
    assert isinstance(pattern["practice_themes"], list)
    assert {(move["game_id"], move["ply"]) for move in pattern["supporting_moves"]} == {
        (game_id, 1) for game_id in game_ids
    }
    assert all(move["loss"] is None for move in pattern["supporting_moves"])


def test_patterns_distinguishes_insufficient_data_from_no_recurrence(
    client: TestClient, db_session: Session
) -> None:
    player_id = "sparse-player"
    token = create_player_token(client, player_id)
    for diagnosis in ("threat_processing.missed_capture", "threat_processing.missed_check"):
        game = create_report_game(db_session, player_id)
        create_report_lesson(db_session, game, diagnosis)
    db_session.commit()

    sparse = client.get(
        f"/v1/players/{player_id}/patterns",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sparse.json()["status"] == "insufficient_data"

    game = create_report_game(db_session, player_id)
    create_report_lesson(db_session, game, "threat_processing.missed_direct_threat")
    db_session.commit()

    no_recurrence = client.get(
        f"/v1/players/{player_id}/patterns",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert no_recurrence.json()["status"] == "no_recurring_diagnosis"


def test_openings_report_uses_owned_game_families(client: TestClient, db_session: Session) -> None:
    player_id = "opening-player"
    token = create_player_token(client, player_id)
    game = Game(
        pgn="1. e4 e5 2. Nf3 Nc6 3. Bc4",
        moves=["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
        owner_player_id=player_id,
        white=player_id,
        black="Opponent",
        result="1-0",
    )
    imported = Game(
        pgn="1. e4 e5 2. Nf3 Nc6 3. Bc4",
        moves=["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
        owner_player_id=player_id,
        white="Imported White",
        black="Imported Black",
        result="0-1",
    )
    db_session.add_all([game, imported])
    db_session.flush()
    create_report_lesson(db_session, game, "threat_processing.missed_capture")
    db_session.commit()

    response = client.get(
        f"/v1/reports/openings?player_id={player_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["openings"] == [
        {
            "family_id": "italian",
            "name": "Italian Game",
            "game_count": 2,
            "error_rate": 0.5,
            "eligible_result_count": 1,
            "excluded_result_count": 1,
            "win_rate": 1.0,
        }
    ]


def test_weekly_report_excludes_retired_mastery(client: TestClient, db_session: Session) -> None:
    player_id = "weekly-player"
    token = create_player_token(client, player_id)
    db_session.add(Game(pgn="1. e4 e5", owner_player_id=player_id))
    db_session.add(
        SkillState(player_id=player_id, concept_code="tactics.fork.knight", alpha=3, beta=1)
    )
    db_session.add(
        SkillState(
            player_id=player_id,
            concept_code="retired.concept",
            alpha=9,
            beta=1,
            retired_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    response = client.get(
        f"/v1/reports/weekly?player_id={player_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    report = response.json()
    assert report["games_played"] == 1
    assert report["active_concepts_observed"] == 1
    assert report["active_mastery"] == [
        {"concept_code": "tactics.fork.knight", "name": "Knight fork", "mastery": 0.75}
    ]
    assert report["top_recurring_diagnosis"] is None


def test_weekly_report_selects_most_frequent_recurring_diagnosis(
    client: TestClient, db_session: Session
) -> None:
    player_id = "weekly-recurring-player"
    token = create_player_token(client, player_id)
    for diagnosis, count in (
        ("threat_processing.missed_capture", 4),
        ("threat_processing.missed_check", 3),
    ):
        for _ in range(count):
            game = create_report_game(db_session, player_id)
            create_report_lesson(db_session, game, diagnosis)
    db_session.commit()

    response = client.get(
        f"/v1/reports/weekly?player_id={player_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert (
        response.json()["top_recurring_diagnosis"]["diagnosis"]
        == "threat_processing.missed_capture"
    )
    assert response.json()["top_recurring_diagnosis"]["name"] == "Missed capture"


def test_progress_reports_taxonomy_names_for_stored_skills(
    client: TestClient, db_session: Session
) -> None:
    player_id = "progress-player"
    token = create_player_token(client, player_id)
    db_session.add_all(
        [
            SkillState(
                player_id=player_id,
                concept_code="board_awareness.hanging_piece",
                alpha=1,
                beta=3,
            ),
            SkillState(player_id=player_id, concept_code="legacy.unknown"),
        ]
    )
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/progress",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    skills = {skill["concept"]: skill for skill in response.json()["skills"]}
    assert skills["board_awareness.hanging_piece"] == {
        "concept": "board_awareness.hanging_piece",
        "name": "Hanging piece",
        "mastery": 0.25,
        "uncertainty": 0.0375,
    }
    assert skills["legacy.unknown"]["name"] == "Unclassified skill"


@pytest.mark.parametrize("diagnosis", [*sorted(TRUSTED_CODES), "tactics.pin"])
def test_summaries_exclude_markerless_legacy_and_untrusted_diagnoses(
    client: TestClient, db_session: Session, diagnosis: str
) -> None:
    player_id = "hidden-report-player"
    token = create_player_token(client, player_id)
    lessons = []
    for _ in range(3):
        game = create_report_game(db_session, player_id)
        game.pgn = "1. e4 e5 2. Nf3 Nc6 3. Bc4"
        game.moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]
        lessons.append(
            create_report_lesson(db_session, game, diagnosis, marked=diagnosis == "tactics.pin")
        )
    db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}
    patterns = client.get(f"/v1/players/{player_id}/patterns", headers=headers)
    assert patterns.status_code == 200
    assert patterns.json()["status"] == "no_recurring_diagnosis"
    assert patterns.json()["eligible_game_count"] == 3
    assert patterns.json()["recurring_diagnoses"] == []
    openings = client.get(f"/v1/reports/openings?player_id={player_id}", headers=headers)
    assert openings.status_code == 200
    assert openings.json()["openings"][0]["error_rate"] == 0.0
    weekly = client.get(f"/v1/reports/weekly?player_id={player_id}", headers=headers)
    assert weekly.status_code == 200
    assert weekly.json()["games_played"] == 3
    assert weekly.json()["top_recurring_diagnosis"] is None
    for lesson in lessons:
        retained = db_session.get(PersistedLessonOpportunity, lesson.id)
        assert retained is not None and retained.lesson_spec["diagnosis"]["primary"] == diagnosis


@pytest.mark.parametrize("owner_color", ["white", "black"])
def test_patterns_count_repeated_epd_owner_plies_once_per_game_ply_code(
    client: TestClient,
    db_session: Session,
    owner_color: Literal["white", "black"],
) -> None:
    player_id = f"repeated-pattern-{owner_color}"
    token = create_player_token(client, player_id)
    expected = set()
    for _ in range(3):
        game = create_report_game(db_session, player_id, owner_color=owner_color)
        owner_ply = 1 if owner_color == "white" else 2
        sources = []
        for ply in (owner_ply, owner_ply + 4):
            lesson = create_report_lesson(
                db_session, game, "threat_processing.missed_capture", ply=ply, with_review=True
            )
            source = db_session.get(Position, lesson.source_position_id)
            assert source is not None
            sources.append(source)
            row = db_session.exec(
                select(GameMoveReview).where(
                    GameMoveReview.game_id == game.id, GameMoveReview.ply == ply
                )
            ).one()
            assert lesson.verification_analysis_id != row.before_analysis_id
            expected.add((str(game.id), ply))
            create_report_lesson(
                db_session, game, "threat_processing.missed_capture", ply=ply, source=source
            )
        assert sources[0].canonical_id == sources[1].canonical_id
        assert sources[0].fen != sources[1].fen
        create_report_lesson(
            db_session,
            game,
            "threat_processing.missed_capture",
            ply=2 if owner_color == "white" else 1,
            with_review=True,
        )
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/patterns", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    report = response.json()
    assert report["eligible_game_count"] == 3
    assert report["status"] == "recurring_diagnosis"
    pattern = report["recurring_diagnoses"][0]
    assert pattern["occurrence_count"] == 3
    assert pattern["total_occurrences"] == 6
    assert pattern["scored_occurrences"] == 6
    assert pattern["observed_loss"] == pytest.approx(3.0)
    assert {(move["game_id"], move["ply"]) for move in pattern["supporting_moves"]} == expected
    assert len(pattern["supporting_moves"]) == 6
    assert all(move["loss"] == pytest.approx(0.5) for move in pattern["supporting_moves"])
    selected_refs = set()
    for move in pattern["supporting_moves"]:
        lesson = db_session.get(PersistedLessonOpportunity, UUID(move["lesson_id"]))
        assert lesson is not None
        assert str(lesson.game_id) == move["game_id"]
        selected_refs.update(lesson.lesson_spec["diagnosis"]["evidence_refs"])
    assert pattern["evidence_references"] == sorted(selected_refs)
    assert len(selected_refs) == 6


@pytest.mark.parametrize(
    "ineligible", ["pending", "running", "failed", "missing_job", "unknown_color", "foreign"]
)
def test_patterns_require_three_distinct_owned_completed_known_color_games(
    client: TestClient, db_session: Session, ineligible: str
) -> None:
    player_id = "eligibility-pattern"
    token = create_player_token(client, player_id)
    games = [create_report_game(db_session, player_id) for _ in range(3)]
    for game in games:
        create_report_lesson(db_session, game, "threat_processing.missed_capture")
    job = db_session.exec(select(AnalysisJob).where(AnalysisJob.game_id == games[-1].id)).one()
    if ineligible == "missing_job":
        db_session.delete(job)
    elif ineligible == "unknown_color":
        games[-1].owner_color = None
    elif ineligible == "foreign":
        games[-1].owner_player_id = "another-owner"
    else:
        job.status = ineligible
    db_session.add(AnalysisJob(game_id=games[0].id, status="completed"))
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/patterns", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["eligible_game_count"] == 2
    assert response.json()["status"] == "insufficient_data"
    assert response.json()["recurring_diagnoses"] == []


@pytest.mark.parametrize("missing", ["review", "before_probability", "after_probability"])
def test_patterns_keep_missing_review_or_probability_unscored(
    client: TestClient, db_session: Session, missing: str
) -> None:
    player_id = "unscored-pattern"
    token = create_player_token(client, player_id)
    for _ in range(3):
        game = create_report_game(db_session, player_id)
        create_report_lesson(
            db_session,
            game,
            "threat_processing.missed_capture",
            with_review=missing != "review",
            before_probability=None if missing == "before_probability" else 0.75,
            after_probability=None if missing == "after_probability" else 0.75,
        )
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/patterns", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    pattern = response.json()["recurring_diagnoses"][0]
    assert pattern["occurrence_count"] == pattern["total_occurrences"] == 3
    assert pattern["scored_occurrences"] == 0
    assert pattern["observed_loss"] is None
    assert len(pattern["supporting_moves"]) == 3
    assert all(move["ply"] == 1 and move["loss"] is None for move in pattern["supporting_moves"])


@pytest.mark.parametrize(
    "invalid",
    [
        "invalid_verification",
        "missing_verification",
        "wrong_verification_source",
        "foreign_source_game",
        "foreign_lesson_player",
        "wrong_spec_source",
        "missing_schedule",
        "retired_schedule",
        "retired_skill",
    ],
)
def test_patterns_remove_later_invalidated_or_retired_evidence_on_next_read(
    client: TestClient, db_session: Session, invalid: str
) -> None:
    player_id = "invalidated-pattern"
    token = create_player_token(client, player_id)
    lessons = []
    for _ in range(3):
        game = create_report_game(db_session, player_id)
        lessons.append(
            create_report_lesson(
                db_session, game, "threat_processing.missed_capture", with_review=True
            )
        )
    db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}
    before = client.get(f"/v1/players/{player_id}/patterns", headers=headers)
    assert before.status_code == 200
    assert before.json()["recurring_diagnoses"][0]["scored_occurrences"] == 3

    lesson = lessons[-1]
    analysis = db_session.get(EngineAnalysis, lesson.verification_analysis_id)
    source = db_session.get(Position, lesson.source_position_id)
    schedule = db_session.get(ReviewSchedule, (player_id, str(lesson.id)))
    assert analysis is not None and source is not None and schedule is not None
    if invalid == "invalid_verification":
        lesson.verification_status = "invalid"
    elif invalid == "missing_verification":
        lesson.verification_analysis_id = None
    elif invalid == "wrong_verification_source":
        analysis.position_id = lessons[0].source_position_id
    elif invalid == "foreign_source_game":
        source.game_id = lessons[0].game_id
    elif invalid == "foreign_lesson_player":
        lesson.player_id = "another-owner"
    elif invalid == "wrong_spec_source":
        lesson.lesson_spec = {
            **lesson.lesson_spec,
            "source": {"kind": "custom", "fen": chess.STARTING_FEN.replace(" w ", " b ")},
        }
    elif invalid == "missing_schedule":
        db_session.delete(schedule)
    elif invalid == "retired_schedule":
        schedule.retired_at = datetime.now(UTC)
    else:
        db_session.add(
            SkillState(
                player_id=player_id,
                concept_code="threat_processing.missed_capture",
                retired_at=datetime.now(UTC),
            )
        )
    db_session.commit()

    after = client.get(f"/v1/players/{player_id}/patterns", headers=headers)
    assert after.status_code == 200
    assert after.json()["eligible_game_count"] == 3
    assert after.json()["status"] == "no_recurring_diagnosis"
    assert after.json()["recurring_diagnoses"] == []
    assert db_session.get(PersistedLessonOpportunity, lesson.id) is not None


@pytest.mark.parametrize(
    ("counts", "losses", "expected"),
    [
        ((3, 4, 5), (0.5, 0.1, None), (0, 1, 2)),
        ((3, 4, 5), (0.4, 0.35, None), (1, 0, 2)),
        ((3, 4, 5), (0.5, 0.375, None), (1, 0, 2)),
        ((3, 3, 4), (0.25, 0.25, None), (0, 1, 2)),
        ((3, 4, 5), (0.0, None, None), (0, 2, 1)),
        ((3, 3, 3), (None, None, None), (0, 1, 2)),
    ],
)
def test_patterns_and_weekly_rank_sum_loss_then_games_then_code_with_unscored_last(
    client: TestClient,
    db_session: Session,
    counts: tuple[int, int, int],
    losses: tuple[float | None, float | None, float | None],
    expected: tuple[int, int, int],
) -> None:
    player_id = "ranked-pattern"
    token = create_player_token(client, player_id)
    codes = (
        "threat_processing.missed_capture",
        "threat_processing.missed_check",
        "threat_processing.missed_direct_threat",
    )
    for code, count, loss in zip(codes, counts, losses, strict=True):
        for _ in range(count):
            game = create_report_game(db_session, player_id)
            create_report_lesson(
                db_session,
                game,
                code,
                with_review=True,
                before_probability=1.0,
                after_probability=loss,
            )
    db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get(f"/v1/players/{player_id}/patterns", headers=headers)
    assert response.status_code == 200
    report = response.json()
    assert report["eligible_game_count"] == sum(counts)
    patterns = report["recurring_diagnoses"]
    assert [pattern["diagnosis"] for pattern in patterns] == [codes[index] for index in expected]
    for pattern, index in zip(patterns, expected, strict=True):
        assert pattern["occurrence_count"] == counts[index]
        assert pattern["total_occurrences"] == counts[index]
        loss = losses[index]
        if loss is None:
            assert pattern["observed_loss"] is None
            assert pattern["scored_occurrences"] == 0
        else:
            assert pattern["observed_loss"] == pytest.approx(counts[index] * loss)
            assert pattern["scored_occurrences"] == counts[index]
    weekly = client.get(f"/v1/reports/weekly?player_id={player_id}", headers=headers)
    assert weekly.status_code == 200
    assert weekly.json()["top_recurring_diagnosis"] == patterns[0]


def test_patterns_report_partial_scored_coverage_without_imputing_missing_loss(
    client: TestClient, db_session: Session
) -> None:
    player_id = "partial-pattern"
    token = create_player_token(client, player_id)
    for loss in (0.5, None, 0.0):
        game = create_report_game(db_session, player_id)
        create_report_lesson(
            db_session,
            game,
            "threat_processing.missed_capture",
            with_review=True,
            before_probability=1.0,
            after_probability=loss,
        )
    db_session.commit()
    response = client.get(
        f"/v1/players/{player_id}/patterns", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    pattern = response.json()["recurring_diagnoses"][0]
    assert pattern["total_occurrences"] == 3
    assert pattern["scored_occurrences"] == 2
    assert pattern["observed_loss"] == pytest.approx(0.5)
    assert sum(move["loss"] is None for move in pattern["supporting_moves"]) == 1


def test_coach_patterns_keep_cost_support_coverage_but_not_owner_actions(
    client: TestClient, db_session: Session
) -> None:
    player_id = "pattern-student"
    student_token = create_player_token(client, player_id)
    coach_token = create_player_token(client, "pattern-coach")
    link = client.post(
        f"/v1/coaches/pattern-coach/students/{player_id}",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert link.status_code == 201
    for _ in range(3):
        game = create_report_game(db_session, player_id)
        create_report_lesson(db_session, game, "threat_processing.missed_capture", with_review=True)
    db_session.commit()
    owner = client.get(
        f"/v1/players/{player_id}/patterns",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    dashboard = client.get(
        "/v1/coaches/pattern-coach/dashboard",
        headers={"Authorization": f"Bearer {coach_token}"},
    )
    assert owner.status_code == dashboard.status_code == 200
    summary = dashboard.json()["students"][0]["patterns"]
    assert summary["eligible_game_count"] == 3
    pattern = summary["recurring_diagnoses"][0]
    owner_pattern = owner.json()["recurring_diagnoses"][0]
    for key in (
        "diagnosis",
        "name",
        "template",
        "occurrence_count",
        "total_occurrences",
        "scored_occurrences",
        "observed_loss",
    ):
        assert pattern[key] == owner_pattern[key]
    assert "supporting_moves" not in pattern
    assert "practice_themes" not in pattern
    assert "practice_actions" not in pattern
    assert (
        client.get(
            f"/v1/players/{player_id}/patterns",
            headers={"Authorization": f"Bearer {coach_token}"},
        ).status_code
        == 403
    )


def test_pattern_and_weekly_gets_do_not_write_reverify_or_search(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    player_id = "read-only-pattern"
    token = create_player_token(client, player_id)
    for _ in range(3):
        game = create_report_game(db_session, player_id)
        create_report_lesson(db_session, game, "threat_processing.missed_capture", with_review=True)
    db_session.commit()

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Pattern projection searched, reverified or mutated persistence")

    for module, name in (
        (game_review, "_key_moments"),
        (game_review, "build_game_review"),
        (game_review, "_reverify_persisted_lesson"),
        (learning, "_reverify_persisted_lesson"),
        (StockfishAdapter, "analyze_position"),
        (PoolBoundAdapter, "analyze_position"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    for name in ("_key_moments", "build_game_review", "_reverify_persisted_lesson"):
        if hasattr(reports, name):
            monkeypatch.setattr(reports, name, forbidden)
    monkeypatch.setattr(db_session, "add", forbidden)
    monkeypatch.setattr(db_session, "add_all", forbidden)
    monkeypatch.setattr(db_session, "delete", forbidden)
    monkeypatch.setattr(db_session, "commit", forbidden)
    statements: list[str] = []

    def record_statement(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement.lstrip().split(maxsplit=1)[0].upper())

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        for _ in range(2):
            for path in (
                f"/v1/players/{player_id}/patterns",
                f"/v1/reports/weekly?player_id={player_id}",
            ):
                response = client.get(path, headers={"Authorization": f"Bearer {token}"})
                assert response.status_code == 200
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)
    assert statements
    assert set(statements) == {"SELECT"}


@pytest.mark.parametrize("wrong_join", ["source", "game"])
def test_patterns_never_score_a_review_from_the_wrong_game_or_source(
    client: TestClient, db_session: Session, wrong_join: str
) -> None:
    player_id = "review-join-pattern"
    token = create_player_token(client, player_id)
    foreign = create_report_game(db_session, "foreign-review-owner")
    for _ in range(3):
        game = create_report_game(db_session, player_id)
        create_report_lesson(db_session, game, "threat_processing.missed_capture", with_review=True)
        row = db_session.exec(select(GameMoveReview).where(GameMoveReview.game_id == game.id)).one()
        if wrong_join == "source":
            row.source_position_id = row.reached_position_id
        else:
            # Give every foreign occurrence a distinct ply to preserve the table key.
            row.game_id = foreign.id
            row.ply = (
                len(
                    db_session.exec(
                        select(GameMoveReview).where(GameMoveReview.game_id == foreign.id)
                    ).all()
                )
                + 1
            )
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/patterns", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    report = response.json()
    assert report["eligible_game_count"] == 3
    pattern = report["recurring_diagnoses"][0]
    assert pattern["occurrence_count"] == pattern["total_occurrences"] == 3
    assert pattern["scored_occurrences"] == 0
    assert pattern["observed_loss"] is None
    assert all(move["loss"] is None for move in pattern["supporting_moves"])


def test_patterns_allow_stored_unverified_status_and_ignore_other_players_retirement(
    client: TestClient, db_session: Session
) -> None:
    player_id = "legacy-status-pattern"
    token = create_player_token(client, player_id)
    for _ in range(3):
        game = create_report_game(db_session, player_id)
        lesson = create_report_lesson(
            db_session, game, "threat_processing.missed_capture", with_review=True
        )
        lesson.verification_status = "unverified"
    db_session.add(
        SkillState(
            player_id="another-owner",
            concept_code="threat_processing.missed_capture",
            retired_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/patterns", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    pattern = response.json()["recurring_diagnoses"][0]
    assert pattern["occurrence_count"] == pattern["scored_occurrences"] == 3
    for lesson in db_session.exec(
        select(PersistedLessonOpportunity).where(PersistedLessonOpportunity.player_id == player_id)
    ).all():
        analysis = db_session.get(EngineAnalysis, lesson.verification_analysis_id)
        assert analysis is not None
        analysis.raw_result = []
    db_session.commit()
    unscored_analysis = client.get(
        f"/v1/players/{player_id}/patterns", headers={"Authorization": f"Bearer {token}"}
    )
    assert unscored_analysis.status_code == 200
    assert unscored_analysis.json() == response.json()
