from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import chess
import pytest
from chess_lesson_spec import Explanation

import scan64.cli.analyse as analyse
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import CandidatePosition, FastPassResult
from scan64.explanations.facts import build_narrator_facts
from scan64.learning.diagnosis.models import DiagnosisCandidate
from scan64.providers.engine import ENGINE_CONFIG_ENV


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
    profile = tmp_path / "engine.toml"
    profile.write_text("[engine]\nname = 'second'\nbinary_path = 'second'\n", encoding="utf-8")
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(profile))

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
    # The lesson names the engine the CLI's adapter was configured with.
    assert generated.call_args.kwargs["engine"] == "second"


@pytest.mark.asyncio
async def test_a_cli_lesson_has_the_games_trace_in_its_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#492: the narrator facts of a CLI lesson hold the game's themes and turning point."""
    fen = "4r1k1/8/8/8/4Q3/8/8/K7 w - - 0 1"
    pgn = tmp_path / "game.pgn"
    pgn.write_text(f'[SetUp "1"]\n[FEN "{fen}"]\n\n1. Qe2 *')
    board = chess.Board(fen)
    before = EngineAnalysis(config={}, raw_result=[{"pv": ["e4e8"], "score_cp": 20}])
    board.push_uci("e4e2")
    after = EngineAnalysis(config={}, raw_result=[{"pv": ["e8e2"], "score_cp": 900}])
    candidate = CandidatePosition(board.fen(), 0, before, after, 920)
    monkeypatch.setattr(
        analyse.FastPassOrchestrator,
        "run_fast_pass",
        AsyncMock(return_value=FastPassResult([candidate], [before, after])),
    )
    adapter = Mock(analyze_position=AsyncMock(return_value=after))
    monkeypatch.setattr(analyse, "StockfishAdapter", Mock(return_value=adapter))
    monkeypatch.setattr(analyse, "analyze_lesson_source", AsyncMock(return_value=_analysis("e4e8")))
    resolve = AsyncMock(return_value=Explanation(text="Unit lesson"))
    monkeypatch.setattr(analyse, "resolve_explanation", resolve)

    await analyse.analyse_command([str(pgn)])

    call = resolve.await_args
    assert call is not None
    facts = build_narrator_facts(*call.args, call.kwargs["game"])
    assert facts.game is not None and facts.game.turning_point is not None
    assert facts.game.turning_point.is_mistake
