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
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content import puzzle_practice
from scan64.learning.diagnosis import visibility
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evaluation.m94_protocol import CODES
from scan64.learning.evaluation.real_games import M94_CORPUS
from scan64.learning.evidence.models import Evidence
from scan64.learning.evidence.motifs import CAPABILITIES, motif_payloads
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from tests.integration.test_m93_product_visibility import seed_recorded_motifs
from tests.integration.test_puzzle_practice import seed
from tests.integration.test_puzzle_runs import _start
from tests.integration.test_reports_from_real_data import create_player_token


def _seed_synthetic_motif_history(session: Session, player: str, code: str) -> list[str]:
    """Three eligible synthetic games isolate policy, never measured-corpus truth."""
    cases = json.loads(
        (Path(__file__).parents[1] / "fixtures/m94/synthetic_cases.json").read_text()
    )
    case = next(item for item in cases if item["code"] == code)
    before = chess.Board(case["fen"])
    played = before.parse_san(case["played"])
    proof = next(
        item
        for item in motif_payloads(before, case["line"], case["mate"])
        if f"tactics.{item['tactic_type']}" == code
    )
    game_ids = []
    for index in range(3):
        pgn = chess.pgn.Game()
        pgn.setup(before)
        pgn.add_variation(played)
        pgn.headers.update(Site=f"synthetic-m94-{player}-{index}", Result="0-1")
        game = Game(
            pgn=str(pgn),
            moves=[played.uci()],
            owner_player_id=player,
            owner_color="white" if before.turn else "black",
            result="0-1",
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
        analysis = EngineAnalysis(
            position_id=position.id,
            config={"nodes": 1_000_000, "multipv": 4, "engine_name": "Stockfish 19"},
            raw_result=[
                {
                    "pv": case["line"],
                    "score_mate": case["mate"],
                    "score_cp": 900 if case["mate"] is None else None,
                }
            ],
        )
        session.add(analysis)
        session.flush()
        evidence = Evidence(
            evidence_id=f"synthetic-m94-{position.id}",
            kind="missed_tactic",
            position_id=str(position.id),
            engine_analysis_id=str(analysis.id),
            claim="Synthetic product-policy fixture, not a measurement",
            payload={
                **proof,
                "before_fen": before.fen(),
                "fast_line": case["line"],
                "played_move": case["played"],
                "best_move": case["line"][0],
                "source_score_mate": case["mate"],
                "diagnosis_capabilities": {code: CAPABILITIES[code]},
            },
        )
        session.add(evidence)
        lesson = PersistedLessonOpportunity(
            game_id=game.id,
            player_id=player,
            source_position_id=position.id,
            verification_status="verified",
            verification_analysis_id=analysis.id,
            lesson_spec={
                "schema_version": "0.2.0",
                "lesson_id": str(position.id),
                "source": {"kind": "custom", "fen": before.fen()},
                "diagnosis": {
                    "primary": code,
                    "secondary": [],
                    "confidence": 1.0,
                    "evidence_refs": [evidence.evidence_id],
                },
                "objective": {"type": "find_best_move", "instruction": "Find the motif."},
                "interaction": {
                    "input": "click",
                    "maximum_attempts": 3,
                    "accepted_moves": [{"san": before.san(chess.Move.from_uci(case["line"][0]))}],
                },
                "verification": {"status": "verified", "engine": "Stockfish 19"},
                "hints": [],
                "explanation": {"text": "Synthetic eligibility fixture; not measured evidence."},
            },
        )
        session.add(lesson)
        session.flush()
        session.add(
            ReviewSchedule(
                player_id=player,
                item_id=str(lesson.id),
                skill_id=code,
                next_review_at=datetime.now(UTC),
            )
        )
        game_ids.append(str(game.id))
    session.commit()
    return sorted(game_ids)


@pytest.mark.parametrize("code", sorted(CODES))
def test_patterns_filter_trust_with_three_eligible_synthetic_games(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    player = f"m94-synthetic-{code}"
    token = create_player_token(client, player)
    games = _seed_synthetic_motif_history(db_session, player, code)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(visibility, "TRUSTED_CODES", TRUSTED_CODES | {code})
    eligible = client.get(f"/v1/players/{player}/patterns", headers=headers)
    assert eligible.status_code == 200, eligible.text
    rows = eligible.json()["recurring_diagnoses"]
    assert [row["diagnosis"] for row in rows] == [code]
    assert rows[0]["game_ids"] == games
    assert rows[0]["total_occurrences"] == 3
    monkeypatch.setattr(visibility, "TRUSTED_CODES", TRUSTED_CODES - {code})
    hidden = client.get(f"/v1/players/{player}/patterns", headers=headers)
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["recurring_diagnoses"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(CODES))
async def test_recorded_m94_patterns_and_training_follow_observed_trust(
    client: TestClient, db_session: Session, code: str
) -> None:
    player = f"m94-recorded-{code}"
    token = create_player_token(client, player)
    saved = await seed_recorded_motifs(
        db_session, player, corpus=M94_CORPUS, codes=frozenset({code}), evidence_prefix="m94"
    )
    headers = {"Authorization": f"Bearer {token}"}
    patterns = client.get(f"/v1/players/{player}/patterns", headers=headers)
    assert patterns.status_code == 200, patterns.text
    expected = {code} if code in TRUSTED_CODES and len(saved[code]) == 3 else set()
    assert {row["diagnosis"] for row in patterns.json()["recurring_diagnoses"]} == expected
    training = client.get("/v1/learning/session", params={"player_id": player}, headers=headers)
    assert training.status_code == 200, training.text
    lessons = training.json()["lessons"]
    expected_training = {code} if code in TRUSTED_CODES and saved[code] else set()
    assert {lesson["diagnosis"]["primary"] for lesson in lessons} == expected_training
    if expected_training:
        assert len(lessons) == len(saved[code])


@pytest.mark.parametrize(
    "code,theme",
    [
        ("tactics.skewer", "skewer"),
        ("tactics.discovered_attack", "discoveredAttack"),
        ("tactics.back_rank", "backRankMate"),
        ("tactics.fork.knight", "fork"),
    ],
)
def test_m94_puzzle_weighting_and_existing_fork_follow_actual_verdicts(
    client: TestClient, db_session: Session, code: str, theme: str
) -> None:
    player = f"m94-weight-{theme}"
    token = create_player_token(client, player)
    db_session.add(SkillState(player_id=player, concept_code=code, alpha=1, beta=10))
    db_session.commit()
    seed(db_session, f"m94-theme-{theme}", themes=[theme])
    run: dict[str, Any] = _start(client, {"Authorization": f"Bearer {token}"})
    mapped = code in puzzle_practice.THEME_SKILLS.get(theme, frozenset())
    assert mapped == (code in TRUSTED_CODES)
    assert run["selection_reason"] == (
        "weak" if code in TRUSTED_CODES and mapped else "exploration"
    )


@pytest.mark.parametrize(
    "code,theme",
    [
        ("tactics.skewer", "skewer"),
        ("tactics.discovered_attack", "discoveredAttack"),
        ("tactics.back_rank", "backRankMate"),
    ],
)
@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("mapped", [False, True])
def test_puzzle_weak_weighting_requires_both_mapping_and_trust(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    theme: str,
    trusted: bool,
    mapped: bool,
) -> None:
    player = f"m94-policy-{theme}-{trusted}-{mapped}"
    token = create_player_token(client, player)
    monkeypatch.setattr(
        puzzle_practice,
        "TRUSTED_CODES",
        TRUSTED_CODES | {code} if trusted else TRUSTED_CODES - {code},
    )
    if mapped:
        monkeypatch.setitem(puzzle_practice.THEME_SKILLS, theme, frozenset({code}))
    else:
        monkeypatch.delitem(puzzle_practice.THEME_SKILLS, theme, raising=False)
    db_session.add(SkillState(player_id=player, concept_code=code, alpha=1, beta=10))
    db_session.commit()
    seed(db_session, f"m94-policy-{theme}", themes=[theme])
    run: dict[str, Any] = _start(client, {"Authorization": f"Bearer {token}"})
    assert run["selection_reason"] == ("weak" if trusted and mapped else "exploration")


@pytest.mark.asyncio
async def test_promoted_m94_archive_targets_and_existing_fork_restore(
    client: TestClient, db_session: Session
) -> None:
    player = "m94-archive-player"
    token = create_player_token(client, player)
    headers = {"Authorization": f"Bearer {token}"}
    promoted = CODES & TRUSTED_CODES
    for code in sorted(promoted):
        _seed_synthetic_motif_history(db_session, player, code)
    await seed_recorded_motifs(db_session, player, codes=frozenset({"tactics.fork.knight"}))
    expected = promoted | {"tactics.fork.knight"}
    for code in sorted(expected):
        target = client.post(
            f"/v1/players/{player}/habit/targets", json={"code": code}, headers=headers
        )
        assert target.status_code == 200, target.text
    exported = client.post("/v1/exports", json={"player_id": player}, headers=headers)
    assert exported.status_code == 200, exported.text
    archive = exported.json()
    assert {target["code"] for target in archive["pattern_targets"]} == expected
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
    patterns = client.get(f"/v1/players/{player}/patterns", headers=headers)
    assert patterns.status_code == 200, patterns.text
    assert {row["diagnosis"] for row in patterns.json()["recurring_diagnoses"]} == expected
    reexported = client.post("/v1/exports", json={"player_id": player}, headers=headers)
    assert reexported.status_code == 200, reexported.text
    assert reexported.json()["pattern_targets"] == archive["pattern_targets"]
