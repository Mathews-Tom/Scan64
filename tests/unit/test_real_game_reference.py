from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import chess
import chess.pgn
import pytest

from scan64.learning.evaluation import deep_reference
from scan64.learning.evaluation.deep_reference import exchange_gain, reference_labels, replay_line
from scan64.learning.evaluation.real_games import (
    ALLOWED_MATE,
    CORPUS,
    FRESH_CORPUS,
    Score,
    check_scores,
)


def test_trust_gate_does_not_silently_drop_regressed_codes() -> None:
    trusted = frozenset({"measured"})
    assert check_scores({"measured": Score(tp=8, fp=2)}, trusted)
    assert not check_scores({"measured": Score(tp=7, fp=3)}, trusted)
    assert not check_scores({"measured": Score(tp=8, fp=1)}, trusted)
    assert not check_scores({}, trusted)


@pytest.mark.parametrize("fresh_passes", [False, True])
def test_check_uses_fresh80_not_development_for_allowed_mate(
    monkeypatch: pytest.MonkeyPatch, fresh_passes: bool
) -> None:
    from scan64.learning.diagnosis import trust
    from scan64.learning.evaluation import real_games

    async def recorded_measure(path: Path = CORPUS) -> dict[str, Score]:
        if path == FRESH_CORPUS:
            return {ALLOWED_MATE: Score(tp=10 if fresh_passes else 9)}
        return {"legacy": Score(tp=10), ALLOWED_MATE: Score(tp=10 if not fresh_passes else 0)}

    monkeypatch.setattr(real_games, "measure", recorded_measure)
    monkeypatch.setattr(trust, "TRUSTED_CODES", frozenset({"legacy", ALLOWED_MATE}))
    monkeypatch.setattr("sys.argv", ["real_games", "--check"])
    if fresh_passes:
        real_games.main()
    else:
        with pytest.raises(SystemExit, match="Fresh80"):
            real_games.main()


def test_fresh80_is_separate_fixed_holdout() -> None:
    original = json.loads(CORPUS.read_text())
    fresh = json.loads(FRESH_CORPUS.read_text())
    assert fresh["provenance"]["game_count"] == len(fresh["games"]) == 80
    assert hashlib.sha256(CORPUS.read_bytes()).hexdigest() == (
        "c7dc5fd62964b3401bccba6087703154ae9872182b3c0c9f49af0e90f68b255d"
    )
    assert hashlib.sha256(CORPUS.with_name("real_games.pgn").read_bytes()).hexdigest() == (
        "746f4daa743872e697b8780b3ff8681f51f4b06b730c8498cc3d6b33dd88ef05"
    )
    assert original["provenance"]["game_count"] == 40
    assert fresh["provenance"]["preregistration_commit"] == "c4490fa"
    assert fresh["provenance"]["engine"] == original["provenance"]["engine"]
    assert fresh["provenance"]["source_sha256"] == original["provenance"]["source_sha256"]
    assert not {game["id"] for game in original["games"]} & {game["id"] for game in fresh["games"]}
    pgn = FRESH_CORPUS.with_name("fresh80_real_games.pgn")
    assert hashlib.sha256(pgn.read_bytes()).hexdigest() == fresh["provenance"]["pgn_sha256"]
    assert FRESH_CORPUS.stat().st_size <= 8_000_000
    for game in fresh["games"]:
        assert set(game["deep_before"]) == set(game["focused"])


def test_capture_recapture_is_not_hanging_or_a_direct_material_loss() -> None:
    before = chess.Board("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
    after = before.copy()
    after.push_san("Kd2")
    labels = reference_labels(
        before, after, [{"pv": ["e4d5"], "score_cp": 0}], [{"pv": ["d5e4", "d2e3"]}]
    )
    assert "board_awareness.hanging_piece" not in labels
    # Pawn losses are a direct threat, but never called a hanging piece.
    assert "threat_processing.missed_direct_threat" in labels
    exchange = chess.Board("4k3/8/8/3r4/3R4/3Q4/8/4K3 b - - 0 1")
    final = replay_line(exchange, [{"pv": ["d5d4", "d3d4"]}])[-1]
    assert len(final.pieces(chess.ROOK, chess.WHITE)) == 0
    original = exchange.copy()
    original.turn = chess.WHITE
    labels = reference_labels(original, exchange, [], [{"pv": ["d5d4", "d3d4"]}])
    assert "board_awareness.hanging_piece" not in labels
    assert "threat_processing.missed_direct_threat" not in labels


def test_reference_uses_material_and_lines_not_payload_flags() -> None:
    before = chess.Board("4r1k1/8/8/8/8/8/4Q3/K7 w - - 0 1")
    after = before.copy()
    after.push_san("Ka2")
    raw = [{"pv": ["e8e2", "a2b3", "g8f7"]}]
    labels = reference_labels(before, after, [], raw)
    assert "board_awareness.hanging_piece" in labels
    assert "threat_processing.missed_direct_threat" in labels
    raw[0].update(
        {
            "wins_material": False,
            "is_hanging_piece_blunder": False,
            "opponent_executed_threat": False,
        }
    )
    assert reference_labels(before, after, [], raw) == labels


def test_reference_check_and_knight_fork_require_engine_advantage_and_material() -> None:
    before = chess.Board("r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1")
    after = before.copy()
    after.push_san("Nc3")
    pv = ["d5c7", "e8d8", "c7a8"]
    labels = reference_labels(before, after, [{"pv": pv, "score_cp": 500}], [])
    assert "tactics.fork.knight" in labels
    assert "threat_processing.missed_check" in labels
    labels = reference_labels(before, after, [{"pv": pv, "score_cp": 250}], [])
    assert "threat_processing.missed_check" not in labels


def test_reference_missed_capture_includes_the_recapture() -> None:
    before = chess.Board("k2q4/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
    after = before.copy()
    after.push_san("Ke2")
    labels = reference_labels(before, after, [{"pv": ["e4d5", "d8d5"], "score_cp": 0}], [])
    assert "threat_processing.missed_capture" not in labels


def test_reference_delayed_material_loss_is_not_an_immediate_threat() -> None:
    before = chess.Board("4r1k1/8/8/8/8/8/4Q3/K7 w - - 0 1")
    after = before.copy()
    after.push_san("Ka2")
    labels = reference_labels(before, after, [], [{"pv": ["g8h7", "a2b3", "e8e2"]}])
    assert "calculation.stopped_too_early" in labels
    assert "threat_processing.missed_direct_threat" not in labels


def test_reference_king_safety_requires_mate_in_the_line() -> None:
    before = chess.Board()
    before.push_san("f3")
    before.push_san("e5")
    after = before.copy()
    after.push_san("g4")
    assert "positional.king_safety_neglect" in reference_labels(
        before, after, [{"pv": ["e2e4"], "score_cp": 0}], [{"pv": ["d8h4"], "score_mate": 1}]
    )


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("source_mate", [None, 1, 3])
def test_allowed_mate_reference_uses_deep_side_to_move_scores(
    mirror: bool, source_mate: int | None
) -> None:
    before = chess.Board()
    before.push_san("f3")
    before.push_san("e5")
    after = before.copy()
    after.push_san("g4")
    source: list[dict[str, Any]] = [{"pv": ["e2e4"], "score_cp": 0}]
    reply: list[dict[str, Any]] = [{"pv": ["d8h4"], "score_mate": 1, "allowed_mate": False}]
    if source_mate is not None:
        source[0]["score_mate"] = source_mate
    if mirror:
        before = before.mirror()
        after = after.mirror()
        source[0]["pv"] = ["e7e5"]
        reply[0]["pv"] = ["d1h5"]
    assert "threat_processing.allowed_mate" in reference_labels(before, after, source, reply)


@pytest.mark.parametrize("source_mate", [0, -1, -3])
def test_allowed_mate_reference_excludes_preexisting_forced_mate(source_mate: int) -> None:
    before = chess.Board()
    before.push_san("f3")
    before.push_san("e5")
    after = before.copy()
    after.push_san("g4")
    assert "threat_processing.allowed_mate" not in reference_labels(
        before,
        after,
        [{"pv": ["e2e4"], "score_mate": source_mate}],
        [{"pv": ["d8h4"], "score_mate": 1}],
    )


@pytest.mark.parametrize("reply_mate", [None, 0, -1, 4])
def test_allowed_mate_reference_requires_short_positive_reply_mate(
    reply_mate: int | None,
) -> None:
    before = chess.Board()
    before.push_san("f3")
    before.push_san("e5")
    after = before.copy()
    after.push_san("g4")
    reply: list[dict[str, Any]] = [{"pv": ["d8h4"]}]
    if reply_mate is not None:
        reply[0]["score_mate"] = reply_mate
    assert "threat_processing.allowed_mate" not in reference_labels(
        before, after, [{"pv": ["e2e4"], "score_cp": 0}], reply
    )


def test_allowed_mate_reference_requires_source_recording_and_terminal_legal_pv() -> None:
    before = chess.Board()
    before.push_san("f3")
    before.push_san("e5")
    after = before.copy()
    after.push_san("g4")
    source = [{"pv": ["e2e4"], "score_cp": 0}]
    assert "threat_processing.allowed_mate" not in reference_labels(
        before, after, [], [{"pv": ["d8h4"], "score_mate": 1}]
    )
    assert "threat_processing.allowed_mate" not in reference_labels(
        before, after, source, [{"pv": ["b8c6"], "score_mate": 1}]
    )
    with pytest.raises(ValueError, match="Illegal recorded"):
        reference_labels(before, after, source, [{"pv": ["d8h5"], "score_mate": 1}])


def test_reference_development_is_based_on_engine_move_and_home_pieces() -> None:
    before = chess.Board()
    after = before.copy()
    after.push_san("a3")
    labels = reference_labels(before, after, [{"pv": ["g1f3"], "score_cp": 0}], [])
    assert "opening.delayed_development" in labels
    labels = reference_labels(before, after, [{"pv": ["e2e4"], "score_cp": 0}], [])
    assert "opening.delayed_development" not in labels


def test_reference_rejects_illegal_engine_lines() -> None:
    with pytest.raises(ValueError, match="Illegal recorded"):
        replay_line(chess.Board(), [{"pv": ["e2e5"]}])


def test_real_game_provenance_and_size() -> None:
    data = json.loads(CORPUS.read_text())
    provenance = data["provenance"]
    assert provenance["game_count"] >= 30
    assert provenance["license"] == "CC0"
    assert provenance["source_url"].startswith("https://database.lichess.org/")
    assert (
        provenance["source_sha256"]
        == "aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635"
    )
    assert provenance["checksum_url"] == "https://database.lichess.org/standard/sha256sums.txt"
    assert "Stockfish" in provenance["engine"]
    assert provenance["deep_before_nodes"] == provenance["focused_nodes"] == 1_000_000
    assert provenance["deep_before_multipv"] == provenance["focused_multipv"] == 4
    assert data["games"][-1]["id"] == "reference_amateur_game"
    assert CORPUS.stat().st_size <= 2_000_000
    pgn = CORPUS.with_name("real_games.pgn")
    assert hashlib.sha256(pgn.read_bytes()).hexdigest() == provenance["pgn_sha256"]
    count = 0
    with pgn.open() as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            count += 1
            assert not game.errors
            assert all(600 <= int(game.headers[key]) <= 1600 for key in ("WhiteElo", "BlackElo"))
            assert len(list(game.mainline_moves())) >= 20
    assert count == provenance["game_count"]
    for recorded in data["games"]:
        assert set(recorded["deep_before"]) == set(recorded["focused"])
        board = chess.Board()
        for index, raw in enumerate(recorded["fast"]):
            replay_line(board, raw)
            if index < len(recorded["moves"]):
                for line in recorded["deep_before"].get(str(index), []):
                    replay_line(board, [line])
                board.push_san(recorded["moves"][index])
                focused = recorded["focused"].get(str(index))
                if focused is not None:
                    for line in focused:
                        replay_line(board, [line])


@pytest.mark.asyncio
async def test_report_runs_real_detectors_without_a_live_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chess.engine

    from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
    from scan64.learning.diagnosis.trust import TRUSTED_CODES
    from scan64.learning.evaluation.real_games import M79_CORPUS, MISSED_CAPTURE, measure

    async def forbidden_engine(*args: object, **kwargs: object) -> None:
        raise AssertionError("The recorded report must not launch an engine")

    monkeypatch.setattr(chess.engine, "popen_uci", forbidden_engine)
    scores = await measure()
    assert set(scores) == set(SEED_CODES)
    assert sum(score.support for score in scores.values()) > 0
    assert check_scores(scores, TRUSTED_CODES - {ALLOWED_MATE, MISSED_CAPTURE})
    fresh = await measure(FRESH_CORPUS)
    if ALLOWED_MATE in TRUSTED_CODES:
        assert fresh[ALLOWED_MATE].trusted
    m79 = await measure(M79_CORPUS)
    assert (MISSED_CAPTURE in TRUSTED_CODES) is m79[MISSED_CAPTURE].trusted
    assert len(TRUSTED_CODES) >= 3
    assert "board_awareness.hanging_piece" in TRUSTED_CODES


def test_reference_oracle_cannot_import_detector_predicates() -> None:
    tree = ast.parse(inspect.getsource(deep_reference))
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any("evidence" in name or "diagnosis" in name for name in imports)
    assert tuple(inspect.signature(reference_labels).parameters) == (
        "before",
        "after",
        "deep_before",
        "deep_after",
    )


def test_reference_never_attributes_a_later_win_to_an_even_initial_trade() -> None:
    after = chess.Board("5r1k/8/8/8/8/8/2n5/R4RK1 b - - 0 1")
    before = after.copy()
    before.turn = chess.WHITE
    line = [{"pv": ["f8f1", "g1f1", "c2a1"]}]
    assert exchange_gain(replay_line(after, line)) == 0
    labels = reference_labels(before, after, [], line)
    assert "board_awareness.hanging_piece" not in labels
    assert "threat_processing.missed_direct_threat" not in labels


@pytest.mark.asyncio
async def test_report_detects_changed_detector_with_fixed_deep_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scan64.learning.diagnosis.detectors.threat_processing import MissedDirectThreatDetector
    from scan64.learning.evaluation.real_games import measure

    baseline = await measure()

    async def suppress(*args: object, **kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(MissedDirectThreatDetector, "detect", suppress)
    changed = await measure()
    code = "threat_processing.missed_direct_threat"
    assert baseline[code].tp > 0
    assert changed[code].tp == 0
    assert changed[code].fn == baseline[code].fn + baseline[code].tp


def test_recorder_rejects_an_unverified_compressed_source(tmp_path: Path) -> None:
    from scripts.record_diagnosis_reference import verified_pgn

    source = tmp_path / "wrong.pgn.zst"
    source.write_bytes(b"not the published archive")
    with pytest.raises(ValueError, match="published SHA256"):
        with verified_pgn(source):
            pytest.fail("An unverified archive must never reach decompression")
