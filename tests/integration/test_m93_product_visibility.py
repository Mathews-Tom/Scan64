from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.evaluation.m93_protocol import CODES
from scan64.learning.evaluation.real_games import M93_CORPUS, RecordedAdapter
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from tests.integration.test_puzzle_practice import seed
from tests.integration.test_puzzle_runs import _auth, _start
from tests.integration.test_reports_from_real_data import create_player_token


async def seed_recorded_motifs(
    session: Session,
    player_id: str,
    *,
    corpus: Path = M93_CORPUS,
    codes: frozenset[str] = CODES,
    evidence_prefix: str = "m93",
) -> dict[str, list[str]]:
    """Persist actual recorded emissions, including hidden hypotheses, without an engine."""
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    saved: dict[str, list[str]] = {code: [] for code in codes}
    source_games: dict[str, set[str]] = {code: set() for code in codes}
    for recorded in json.loads(corpus.read_text())["games"]:
        adapter = RecordedAdapter(recorded)
        fast = await FastPassOrchestrator(adapter, FastPassConfig(nodes=10_000)).run_fast_pass(
            recorded["moves"]
        )
        for candidate in fast.candidates:
            before = chess.Board(adapter.fens[candidate.move_index])
            after = chess.Board(candidate.fen)
            evidence = compose_candidate_evidence(
                before_board=before,
                after_board=after,
                history_san=recorded["moves"][: candidate.move_index + 1],
                initial_fen=None,
                position_id="recorded",
                fast_analysis=candidate.before_analysis,
                reply_analysis=candidate.after_analysis,
                focused_analysis=EngineAnalysis(
                    raw_result=recorded["focused"][str(candidate.move_index)]
                ),
                mate_source_analysis=EngineAnalysis(
                    config={"nodes": 1_000_000, "multipv": 4},
                    raw_result=recorded["deep_before"][str(candidate.move_index)],
                ),
                played_move=recorded["moves"][candidate.move_index],
                swing_cp=candidate.swing_cp,
            )
            opportunity = LearningOpportunity(
                opportunity_id="recorded",
                position_id="recorded",
                player_id=player_id,
                played_move=recorded["moves"][candidate.move_index],
                engine_eval_before=0,
                engine_eval_after=-candidate.swing_cp / 100,
            )
            for code in sorted(codes):
                if len(saved[code]) == 3 or recorded["id"] in source_games[code]:
                    continue
                detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=code)
                assert isinstance(detector, PatternDetector)
                detected = await detector.detect(
                    opportunity, evidence, PlayerContext(player_id=player_id)
                )
                selected = arbitrate_diagnoses(detected, evidence, trusted_only=False)
                if selected is None:
                    continue
                board = chess.Board()
                moves = []
                for san in recorded["moves"]:
                    move = board.parse_san(san)
                    moves.append(move.uci())
                    board.push(move)
                pgn = chess.pgn.Game.from_board(board)
                pgn.headers["Site"] = recorded["id"]
                game = Game(
                    pgn=str(pgn),
                    moves=moves,
                    owner_player_id=player_id,
                    owner_color="white" if before.turn else "black",
                    result=pgn.headers["Result"],
                    headers=dict(pgn.headers),
                )
                session.add(game)
                session.flush()
                session.add(
                    AnalysisJob(game_id=game.id, status="completed", completed_at=datetime.now(UTC))
                )
                position = Position(
                    game_id=game.id,
                    fen=before.fen(),
                    canonical_id=before.epd(),
                    side_to_move="w" if before.turn else "b",
                )
                session.add(position)
                session.flush()
                verification = EngineAnalysis(
                    position_id=position.id,
                    config={"nodes": 1_000_000, "multipv": 4, "engine_name": "Stockfish 19"},
                    raw_result=recorded["deep_before"][str(candidate.move_index)],
                )
                session.add(verification)
                session.flush()
                primary = selected[0]
                refs = []
                for item in evidence:
                    if item.evidence_id in primary.evidence_ids:
                        proof = Evidence(
                            evidence_id=f"{evidence_prefix}-{position.id}",
                            kind=item.kind,
                            position_id=str(position.id),
                            engine_analysis_id=str(verification.id),
                            claim=item.claim,
                            payload=item.payload,
                        )
                        session.add(proof)
                        refs.append(proof.evidence_id)
                best = chess.Move.from_uci(verification.raw_result[0]["pv"][0])
                lesson = PersistedLessonOpportunity(
                    game_id=game.id,
                    player_id=player_id,
                    source_position_id=position.id,
                    verification_status="verified",
                    verification_analysis_id=verification.id,
                    lesson_spec={
                        "schema_version": "0.2.0",
                        "lesson_id": str(position.id),
                        "source": {"kind": "custom", "fen": before.fen()},
                        "diagnosis": {
                            "primary": code,
                            "secondary": [],
                            "confidence": primary.confidence,
                            "evidence_refs": refs,
                        },
                        "objective": {
                            "type": "find_best_move",
                            "instruction": "Find the recorded best move.",
                        },
                        "interaction": {
                            "input": "click",
                            "maximum_attempts": 3,
                            "accepted_moves": [{"san": before.san(best)}],
                        },
                        "verification": {"status": "verified", "engine": "Stockfish 19"},
                        "hints": [],
                        "explanation": {
                            "text": "Engine-derived operational reference; not coach review."
                        },
                    },
                )
                session.add(lesson)
                session.flush()
                session.add(
                    ReviewSchedule(
                        player_id=player_id,
                        item_id=str(lesson.id),
                        skill_id=code,
                        next_review_at=datetime.now(UTC),
                    )
                )
                assert (visible_game_lesson(lesson, session) is not None) == (code in TRUSTED_CODES)
                saved[code].append(str(lesson.id))
                source_games[code].add(recorded["id"])
        if all(len(ids) == 3 for ids in saved.values()):
            break
    session.commit()
    return saved


@pytest.mark.asyncio
async def test_recorded_motif_lessons_and_patterns_follow_actual_verdicts(
    client: TestClient, db_session: Session
) -> None:
    player = "m93-recorded-player"
    token = create_player_token(client, player)
    saved = await seed_recorded_motifs(db_session, player)
    assert all(len(saved[code]) == 3 for code in CODES & TRUSTED_CODES), saved
    response = client.get(
        f"/v1/players/{player}/patterns", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    codes = {item["diagnosis"] for item in response.json()["recurring_diagnoses"]}
    assert codes == CODES & TRUSTED_CODES
    headers = {"Authorization": f"Bearer {token}"}
    for code in sorted(codes):
        target = client.post(
            f"/v1/players/{player}/habit/targets", json={"code": code}, headers=headers
        )
        assert target.status_code == 200, target.text
    exported = client.post("/v1/exports", json={"player_id": player}, headers=headers)
    assert exported.status_code == 200, exported.text
    archive = exported.json()
    assert {target["code"] for target in archive["pattern_targets"]} == codes
    deleted = client.request(
        "DELETE",
        f"/v1/players/{player}/data",
        json={"dry_run": False, "confirmation": f"delete-{player}"},
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    db_session.expire_all()
    restored = client.post("/v1/imports", json=archive, headers=headers)
    assert restored.status_code == 200, restored.text
    after = client.get(f"/v1/players/{player}/patterns", headers=headers)
    assert after.status_code == 200, after.text
    assert {item["diagnosis"] for item in after.json()["recurring_diagnoses"]} == codes
    reexported = client.post("/v1/exports", json={"player_id": player}, headers=headers).json()
    assert reexported["pattern_targets"] == archive["pattern_targets"]


@pytest.mark.parametrize(
    "code,theme",
    [
        ("tactics.fork.knight", "fork"),
        ("tactics.pin", "pin"),
        ("tactics.overloaded_defender", "deflection"),
    ],
)
def test_motif_puzzle_weighting_follows_actual_verdicts(
    client: TestClient, db_session: Session, code: str, theme: str
) -> None:
    auth = _auth(client)
    db_session.add(SkillState(player_id="puzzle-player", concept_code=code, alpha=1, beta=10))
    db_session.commit()
    seed(db_session, "m93-theme", themes=[theme])
    run: dict[str, Any] = _start(client, auth)
    assert run["selection_reason"] == ("weak" if code in TRUSTED_CODES else "exploration")
