from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import chess
import pytest
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
import scan64.learning.diagnosis.arbitration as arbitration
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview, PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.registry import PluginRegistry
from scan64.learning.tolerance import review_band, review_loss

CODE = "threat_processing.allowed_mate"


class RecordedMateAdapter:
    def __init__(self) -> None:
        self.case: dict[str, Any] = json.loads(
            (Path(__file__).parents[1] / "fixtures/allowed_mate.json").read_text()
        )["positions"][0]
        board = chess.Board(self.case["fen"])
        board.push_uci(self.case["move"])
        self.after_fen = board.fen()
        self.requests: list[tuple[str, int | None, int]] = []

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        self.requests.append((fen, nodes, multipv))
        if nodes == 10_000 and multipv == 1:
            raw = self.case["before"] if fen == self.case["fen"] else self.case["after"]
        elif nodes == 1_000_000 and multipv == 4:
            raw = self.case["mate_source"] if fen == self.case["fen"] else self.case["focused_after"]
        else:
            raise AssertionError(f"Unrecorded analysis request: {fen}, {nodes}, {multipv}")
        assert fen in {self.case["fen"], self.after_fen}
        return EngineAnalysis(config={"nodes": nodes, "multipv": multipv}, raw_result=raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("force_untrusted", [False, True])
async def test_source_proof_persisted_and_material_suppression(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, force_untrusted: bool
) -> None:
    adapter = RecordedMateAdapter()
    monkeypatch.setattr(jobs, "StockfishAdapter", lambda _: adapter)
    if force_untrusted:
        monkeypatch.setattr(arbitration, "TRUSTED_CODES", TRUSTED_CODES - {CODE})
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    game = Game(
        pgn="", headers={"FEN": adapter.case["fen"]}, moves=[adapter.case["move"]],
        owner_color="white", owner_player_id="fixture-player",
    )
    db_session.add(game)
    db_session.commit()
    await jobs.run_analysis_for_game(game, db_session, registry)

    proof = next(
        item for item in db_session.exec(select(Evidence)).all()
        if item.payload.get("blunder_type") == "allowed_mate"
    )
    source = next(
        item for item in db_session.exec(select(EngineAnalysis)).all()
        if str(item.id) == proof.payload["mate_source_analysis_id"]
    )
    assert source.config["nodes"] == 1_000_000
    assert source.raw_result == adapter.case["mate_source"]
    assert adapter.requests.count((adapter.case["fen"], 1_000_000, 4)) == 1
    assert (adapter.case["fen"], 1_000_000, 2) not in adapter.requests
    reviews = db_session.exec(select(GameMoveReview).where(GameMoveReview.game_id == game.id)).all()
    assert len(reviews) == 1
    assert review_band(
        review_loss(reviews[0].before_probability, reviews[0].after_probability)
    ) == "blunder"
    lessons = db_session.exec(
        select(PersistedLessonOpportunity).where(PersistedLessonOpportunity.game_id == game.id)
    ).all()
    expected_visible = CODE in TRUSTED_CODES and not force_untrusted
    assert len(lessons) == int(expected_visible)
    if expected_visible:
        lesson = lessons[0].lesson_spec
        assert lesson["diagnosis"]["primary"] == CODE
        assert "Qb2#" in lesson["explanation"]["text"]
        assert lesson["objective"]["instruction"] == "Find a sound move that does not allow mate."
        assert lesson["interaction"]["accepted_moves"][0]["san"] == "Rxb3+"
        assert lessons[0].verification_analysis_id == source.id
