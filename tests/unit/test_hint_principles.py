import json
from pathlib import Path

import chess
import pytest
from chess_lesson_spec import Diagnosis

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.evaluation.hint_coverage import candidate_tier, main, measure
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.exercises.hints import fill_lesson_hints
from scan64.learning.insights import extract_reasons, insight_step, position_insight

RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/hint_engine_recording.json").read_text()
)


@pytest.mark.parametrize(
    ("fen", "uci", "kind", "applies"),
    [
        (chess.STARTING_FEN, "g1f3", "development", True),
        ("4k3/8/8/8/8/5N2/8/4K3 w - - 0 1", "f3g5", "development", False),
        ("4k3/8/8/8/8/8/8/4K2R w K - 0 1", "e1g1", "castling", True),
        ("4k3/8/8/8/8/8/8/4K2R w K - 0 1", "h1h2", "castling", False),
        (chess.STARTING_FEN, "e2e4", "centre", True),
        (chess.STARTING_FEN, "g1f3", "centre", True),
        (chess.STARTING_FEN, "h2h3", "centre", False),
        ("4k3/8/8/8/3N4/8/8/4K3 w - - 0 1", "d4b3", "centre", False),
        ("4k3/8/8/3Q4/8/8/8/K7 w - - 0 1", "d5d1", "centre", False),
        ("4k3/8/8/8/3B4/8/8/4K3 w - - 0 1", "d4a1", "centre", False),
        ("8/8/8/8/4K3/8/8/k7 w - - 0 1", "e4f3", "centre", False),
        (
            "r3k1r1/p1pp1pbp/4p2p/1Q6/2P1b3/2P4N/PP1N1PPP/R4RK1 b q - 1 15",
            "e4c6",
            "centre",
            False,
        ),
        ("4k3/8/8/8/8/8/P7/R3K3 w - - 0 1", "a1d1", "rook_file", True),
        ("4k3/3p4/8/8/8/8/P7/R3K3 w - - 0 1", "a1d1", "rook_file", True),
        ("4k3/8/8/8/8/8/3P4/R3K3 w - - 0 1", "a1d1", "rook_file", False),
        ("4k3/8/8/8/8/8/8/R3K3 w - - 0 1", "a1a2", "rook_file", False),
        ("r5k1/8/8/8/8/8/5PPP/6K1 w - - 0 1", "h2h3", "king_escape", True),
        ("6k1/8/8/8/8/8/5PPP/6K1 w - - 0 1", "h2h3", "king_escape", False),
        ("r5k1/8/8/8/8/5P2/6PP/6K1 w - - 0 1", "h2h3", "king_escape", False),
        ("r5k1/8/8/8/8/6b1/5PPP/6K1 w - - 0 1", "h2h3", "king_escape", False),
        ("r6k/8/8/8/8/8/5PRP/6K1 w - - 0 1", "g2g3", "king_escape", True),
        ("7k/8/8/8/8/8/5PRP/6K1 w - - 0 1", "g2g3", "king_escape", False),
        ("r6k/8/8/8/8/5b2/5PRP/6K1 w - - 0 1", "g2g3", "king_escape", False),
        ("r5k1/8/8/8/8/8/5PNP/6K1 w - - 0 1", "g2e3", "king_escape", False),
        ("7k/8/8/4P3/8/8/8/K7 w - - 0 1", "e5e6", "passed_pawn", True),
        ("7k/4p3/8/4P3/8/8/8/K7 w - - 0 1", "e5e6", "passed_pawn", False),
        ("7k/3p4/8/4P3/8/8/8/K7 w - - 0 1", "e5e6", "passed_pawn", False),
        ("7k/8/8/3p4/4P3/8/8/K7 w - - 0 1", "e4e5", "passed_pawn", True),
        ("7k/8/8/8/8/5p2/4P3/K7 w - - 0 1", "e2e4", "passed_pawn", True),
    ],
)
@pytest.mark.parametrize("mirror", [False, True])
def test_principle_positive_and_negative_predicates(fen, uci, kind, applies, mirror):
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    if mirror:
        board = board.mirror()
        move = chess.Move(
            chess.square_mirror(move.from_square), chess.square_mirror(move.to_square)
        )
    assert board.is_valid()
    assert move in board.legal_moves
    before = board.fen()
    reasons = extract_reasons(board, [move.uci()])
    assert board.fen() == before
    matches = [reason for reason in reasons if reason.kind == kind]
    assert bool(matches) == applies
    for reason in matches:
        assert reason.tier == "principle"
        assert reason.line == [move.uci()]
        assert not {"wins", "gain", "winning"} & set(reason.description.split())


@pytest.mark.parametrize(
    ("fen", "description"),
    [
        ("4k3/8/8/8/8/8/P7/R3K3 w - - 0 1", "Puts a rook on an open file."),
        ("4k3/3p4/8/8/8/8/P7/R3K3 w - - 0 1", "Puts a rook on a half-open file."),
    ],
)
def test_rook_file_description_matches_enemy_pawns(fen, description):
    reasons = extract_reasons(chess.Board(fen), ["a1d1"])
    assert (
        next(reason.description for reason in reasons if reason.kind == "rook_file") == description
    )


def test_principles_never_admit_an_out_of_tolerance_candidate():
    board = chess.Board()
    result = position_insight(
        board,
        EngineAnalysis(
            raw_result=[
                {"pv": ["h2h3"], "score_cp": 0},
                {"pv": ["g1f3"], "score_cp": -1000},
            ]
        ),
    )
    assert [candidate.move for candidate in result.candidates] == ["h2h3"]
    assert result.candidates[0].fallback


def test_tactical_reason_retains_priority_alongside_principles():
    record = RECORDING["positions"]["A"]
    insight = position_insight(
        chess.Board(record["fen"]), EngineAnalysis(raw_result=record["raw_result"])
    )
    candidate = insight.candidates[0]
    assert any(
        reason.kind == "mate_threat" and reason.tier == "tactical" for reason in candidate.reasons
    )
    assert any(
        reason.kind == "development" and reason.tier == "principle" for reason in candidate.reasons
    )
    assert candidate_tier(candidate) == "tactical"


@pytest.mark.asyncio
async def test_recorded_quiet_principles_and_lesson_labels_do_not_leak_early_moves():
    record = RECORDING["positions"]["D"]
    board = chess.Board(record["fen"])
    analysis = EngineAnalysis(raw_result=record["raw_result"])
    insight = position_insight(board, analysis)
    candidate = next(candidate for candidate in insight.candidates if candidate.san == "Ne5")
    assert any(
        reason.kind == "centre" and reason.tier == "principle" for reason in candidate.reasons
    )
    spec = await generate_exact_replay_exercise(
        fen=board.fen(),
        lesson_id="quiet-principles",
        accepted_moves_san=[item.san for item in insight.candidates],
        diagnosis=Diagnosis(primary="board_awareness.hanging_piece", confidence=1),
    )
    fill_lesson_hints(spec, analysis)
    assert "Principle: Occupies a centre square." in spec.hints[2].text
    for level in (1, 2):
        serialized = insight_step(board, insight, level).model_dump_json()
        for item in insight.candidates:
            assert item.move not in serialized
            assert item.san not in serialized
            assert item.move[2:4] not in serialized
            assert item.move not in spec.hints[level - 1].model_dump_json()
            assert item.move[2:4] not in spec.hints[level - 1].model_dump_json()


def test_coverage_uses_deepest_recording_and_prints_exclusive_shares(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "recorded.json"
    corpus.write_text(
        json.dumps(
            {
                "provenance": {"game_count": 1},
                "games": [
                    {
                        "moves": ["e4"],
                        "fast": [
                            [{"pv": ["h2h3"], "score_cp": 0}],
                            [{"pv": ["h7h6"], "score_cp": 0}],
                        ],
                        "deep_before": {"0": [{"pv": ["g1f3"], "score_cp": 0}]},
                        "focused": {},
                    }
                ],
            }
        )
    )
    games, positions, counts = measure(corpus)
    assert (games, positions) == (1, 2)
    assert counts == {"tactical": 0, "principle": 1, "fallback": 1}
    monkeypatch.setattr("sys.argv", ["hint_coverage", "--corpus", str(corpus)])
    main()
    output = capsys.readouterr().out
    assert "tactical | 0 | 0.00%" in output
    assert "principle | 1 | 50.00%" in output
    assert "fallback | 1 | 50.00%" in output
