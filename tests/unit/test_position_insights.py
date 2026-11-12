import json
from pathlib import Path

import chess
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.insights import extract_reasons, position_insight

RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/hint_engine_recording.json").read_text()
)


def recorded(name: str):
    record = RECORDING["positions"][name]
    return chess.Board(record["fen"]), EngineAnalysis(raw_result=record["raw_result"])


def test_recorded_opening_mate_threat_and_highlight():
    board, analysis = recorded("A")
    result = position_insight(board, analysis)
    assert len(result.candidates) == 3
    first = result.candidates[0]
    assert first.san == "Bc4"
    threat = next(reason for reason in first.reasons if reason.kind == "mate_threat")
    assert threat.line == ["f1c4"]
    assert threat.squares == ["f7"]
    assert "Qxf7#" in threat.description
    # The real reply prevents the threat, not a fabricated mating continuation.
    assert first.line[1] == "g7g6"
    serialized = result.model_dump(mode="json")
    assert any(command.get("square") == "f7" for command in serialized["visualizations"])
    arrows = [
        command for command in serialized["visualizations"] if command["command"] == "draw_arrow"
    ]
    assert [arrow["description"].split(".")[0] for arrow in arrows] == ["1", "2", "3"]
    assert arrows[0]["origin"] == "f1"
    assert arrows[0]["target"] == "c4"


def test_recorded_fork_is_the_only_sound_move():
    board, analysis = recorded("C")
    result = position_insight(board, analysis)
    assert [candidate.san for candidate in result.candidates] == ["Nxc7+"]
    fork = next(reason for reason in result.candidates[0].reasons if reason.kind == "fork")
    assert set(fork.squares) == {"e8", "a8"}
    assert fork.line == ["d5c7"]


@pytest.mark.parametrize("name", ["A", "C", "D"])
def test_every_recorded_reason_has_a_legal_candidate_prefix(name):
    board, analysis = recorded(name)
    result = position_insight(board, analysis)
    assert 1 <= len(result.candidates) <= 3
    for candidate in result.candidates:
        assert candidate.fallback == (not candidate.reasons)
        for reason in candidate.reasons:
            assert reason.line == candidate.line[: len(reason.line)]
            replay = board.copy()
            for uci in reason.line:
                replay.push_uci(uci)
            assert reason in extract_reasons(board, candidate.line)
    if name == "D":
        assert all(not candidate.reasons and candidate.fallback for candidate in result.candidates)


@pytest.mark.parametrize(
    ("fen", "line", "kind"),
    [
        ("4k3/8/8/8/4n3/8/4R3/4K3 w - - 0 1", ["e2e4"], "free_capture"),
        ("4k3/8/8/8/8/2n5/8/1N2K3 w - - 0 1", ["b1d2"], "rescue"),
        ("k7/8/8/3b4/4R3/5N2/8/4K3 w - - 0 1", ["f3d2"], "defence"),
        (chess.STARTING_FEN, ["g1f3"], "development"),
        (RECORDING["positions"]["C"]["fen"], ["e1c1"], "castling"),
        (RECORDING["positions"]["C"]["fen"], ["d5c7"], "check"),
    ],
)
def test_motif_positive_board_witnesses(fen, line, kind):
    assert kind in {reason.kind for reason in extract_reasons(chess.Board(fen), line)}


@pytest.mark.parametrize(
    ("fen", "line", "kind"),
    [
        (chess.STARTING_FEN, ["e2e4"], "mate_threat"),
        (chess.STARTING_FEN, ["e2e4"], "free_capture"),
        (chess.STARTING_FEN, ["g1f3"], "fork"),
        (chess.STARTING_FEN, ["g1f3"], "rescue"),
        (chess.STARTING_FEN, ["g1f3"], "defence"),
        (chess.STARTING_FEN, ["e2e4"], "development"),
        (chess.STARTING_FEN, ["g1f3"], "castling"),
        (chess.STARTING_FEN, ["g1f3"], "check"),
    ],
)
def test_motif_negative_board_witnesses(fen, line, kind):
    assert kind not in {reason.kind for reason in extract_reasons(chess.Board(fen), line)}


def test_forced_mate_needs_score_and_mating_pv():
    board = chess.Board()
    for move in ("f2f3", "e7e5", "g2g4"):
        board.push_uci(move)
    reasons = extract_reasons(board, ["d8h4"], winning_mate=True)
    assert any(reason.kind == "forced_mate" for reason in reasons)
    assert not any(reason.kind == "forced_mate" for reason in extract_reasons(board, ["d8h4"]))
    assert not any(
        reason.kind == "forced_mate"
        for reason in extract_reasons(board, ["b8c6"], winning_mate=True)
    )


def test_cannot_claim_free_capture_when_piece_can_be_recaptured():
    board = chess.Board("k3r3/8/8/8/4n3/8/4R3/4K3 w - - 0 1")
    assert not any(
        reason.kind == "free_capture" for reason in extract_reasons(board, ["e2e4", "e8e4"])
    )


def test_illegal_engine_continuation_is_rejected():
    with pytest.raises(ValueError, match="Illegal engine line"):
        position_insight(
            chess.Board(), EngineAnalysis(raw_result=[{"pv": ["e2e4", "e7e4"], "score_cp": 10}])
        )


@given(st.lists(st.integers(min_value=0, max_value=1000), min_size=1, max_size=20))
@settings(max_examples=50, deadline=None)
def test_reason_prefix_property_for_arbitrary_legal_lines(choices):
    board = chess.Board()
    line = []
    for choice in choices:
        moves = list(board.legal_moves)
        if not moves or board.is_game_over():
            break
        move = moves[choice % len(moves)]
        line.append(move.uci())
        board.push(move)
    result = position_insight(
        chess.Board(), EngineAnalysis(raw_result=[{"pv": line, "score_cp": 0}])
    )
    for candidate in result.candidates:
        for reason in candidate.reasons:
            assert reason.line
            assert candidate.line[: len(reason.line)] == reason.line
