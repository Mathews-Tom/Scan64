from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evaluation.m94_protocol import CODES
from scan64.learning.evaluation.real_games import M94_CORPUS
from scan64.learning.profiling.models import SkillState
from tests.integration.test_m93_product_visibility import seed_recorded_motifs
from tests.integration.test_puzzle_practice import seed
from tests.integration.test_puzzle_runs import _start
from tests.integration.test_reports_from_real_data import create_player_token


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
    expected = {code} if code in TRUSTED_CODES else set()
    assert {row["diagnosis"] for row in patterns.json()["recurring_diagnoses"]} == expected
    training = client.get("/v1/learning/session", params={"player_id": player}, headers=headers)
    assert training.status_code == 200, training.text
    lessons = training.json()["lessons"]
    assert {lesson["diagnosis"]["primary"] for lesson in lessons} == expected
    if code in TRUSTED_CODES:
        assert len(lessons) == len(saved[code]) == 3


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
    assert run["selection_reason"] == ("weak" if code in TRUSTED_CODES else "exploration")


@pytest.mark.asyncio
async def test_promoted_m94_archive_targets_and_existing_fork_restore(
    client: TestClient, db_session: Session
) -> None:
    player = "m94-archive-player"
    token = create_player_token(client, player)
    headers = {"Authorization": f"Bearer {token}"}
    promoted = CODES & TRUSTED_CODES
    if promoted:
        await seed_recorded_motifs(
            db_session, player, corpus=M94_CORPUS, codes=promoted, evidence_prefix="m94"
        )
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
