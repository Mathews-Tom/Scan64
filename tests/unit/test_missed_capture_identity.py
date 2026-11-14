from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import chess
import pytest

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.evidence.composer import compose_candidate_evidence

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/m48/fresh80_recorded.json"


@pytest.mark.parametrize("game_id,ply", [("hgu2kah6", 83), ("09zsd09k", 53), ("hbzk28yq", 64)])
def test_taking_the_recommended_piece_is_not_a_missed_capture(game_id: str, ply: int) -> None:
    corpus: dict[str, Any] = json.loads(FIXTURE.read_text())
    game = next(game for game in corpus["games"] if game["id"].endswith(game_id))
    index = ply - 1
    before = chess.Board()
    for san in game["moves"][:index]:
        before.push_san(san)
    actual = before.parse_san(game["moves"][index])
    fast = EngineAnalysis(raw_result=game["fast"][index])
    assert actual.uci() == fast.raw_result[0]["pv"][0]
    alternative = next(move for move in before.legal_moves if move != actual)

    def evidence_for(move: chess.Move) -> list[dict[str, Any]]:
        san = before.san(move)
        after = before.copy()
        after.push(move)
        return [
            evidence.payload
            for evidence in compose_candidate_evidence(
                before_board=before,
                after_board=after,
                history_san=[san],
                initial_fen=before.fen(),
                position_id="regression",
                fast_analysis=fast,
                reply_analysis=EngineAnalysis(raw_result=[]),
                focused_analysis=EngineAnalysis(raw_result=[]),
                mate_source_analysis=None,
                played_move=san,
                swing_cp=300,
            )
        ]

    assert not any(payload.get("missed_type") == "capture" for payload in evidence_for(actual))
    assert any(payload.get("missed_type") == "capture" for payload in evidence_for(alternative))
