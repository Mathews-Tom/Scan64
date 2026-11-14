from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import chess
import pytest
from chess_lesson_spec import Explanation

import scan64.cli.analyse as analyse
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import CandidatePosition, FastPassResult
from scan64.learning.diagnosis.models import DiagnosisCandidate


def _analysis(*moves: str, mate: int | None = None) -> EngineAnalysis:
    return EngineAnalysis(
        position_id=uuid4(),
        config={"engine_name": "unit", "nodes": 1_000_000, "multipv": 4},
        raw_result=[
            {"pv": [move], **({"score_cp": 0} if mate is None else {"score_mate": mate})}
            for move in moves
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["source", "sound_moves", "played_move"])
async def test_rejected_candidate_does_not_abort_later_cli_lessons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rejection: str,
) -> None:
    pgn = tmp_path / "game.pgn"
    pgn.write_text("1. e4 e5 *")
    board = chess.Board()
    before_white = _analysis("d2d4")
    board.push_uci("e2e4")
    after_white = _analysis("e7e5")
    first = CandidatePosition(board.fen(), 0, before_white, after_white, 300)
    before_black_fen = board.fen()
    board.push_uci("e7e5")
    after_black = _analysis("g1f3")
    second = CandidatePosition(board.fen(), 1, after_white, after_black, 300)
    monkeypatch.setattr(
        analyse.FastPassOrchestrator,
        "run_fast_pass",
        AsyncMock(return_value=FastPassResult([first, second], [])),
    )
    adapter = Mock(analyze_position=AsyncMock(side_effect=[after_white, after_black]))
    monkeypatch.setattr(analyse, "StockfishAdapter", Mock(return_value=adapter))
    monkeypatch.setattr(analyse, "register_seeded_detectors", lambda registry: None)
    selected = DiagnosisCandidate(
        skill_id="threat_processing.allowed_mate", confidence=1.0, evidence_ids=[]
    )
    monkeypatch.setattr(analyse, "arbitrate_diagnoses", lambda *args: (selected, []))
    first_source: EngineAnalysis | ValueError
    if rejection == "source":
        first_source = ValueError("source search rejected")
    elif rejection == "sound_moves":
        first_source = _analysis("d2d4", mate=-2)
    else:
        # The played move is sound but is not the first accepted alternative.
        first_source = _analysis("d2d4", "e2e4")
    source_search = AsyncMock(side_effect=[first_source, _analysis("c7c5")])
    monkeypatch.setattr(analyse, "analyze_lesson_source", source_search)
    monkeypatch.setattr(
        analyse, "resolve_explanation", AsyncMock(return_value=Explanation(text="Unit lesson"))
    )
    generated = AsyncMock(wraps=analyse.generate_exact_replay_exercise)
    monkeypatch.setattr(analyse, "generate_exact_replay_exercise", generated)

    await analyse.analyse_command([str(pgn)], report=True)

    output = capsys.readouterr().out
    assert "Rejected lesson from game.pgn:" in output
    if rejection == "source":
        assert "source search rejected" in output
    elif rejection == "played_move":
        assert "played move is sound" in output
    assert "Generated 1 LessonSpec(s) from 1 game(s)." in output
    assert "Status: SUCCESS" in output
    assert source_search.await_count == 2
    generated.assert_awaited_once()
    assert generated.call_args.kwargs["fen"] == before_black_fen
    assert generated.call_args.kwargs["accepted_moves_san"] == ["c5"]
