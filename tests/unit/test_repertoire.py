from __future__ import annotations

import chess
import pytest

from scan64.learning.repertoire import Repertoire, parse_repertoire_pgn, verify_repertoire_move

PGN = '[Event "Nested repertoire"]\n\n1. e4 (1. d4 d5 (1... Nf6)) e5 (1... c5 2. Nf3) 2. Nf3 *\n'


def test_retains_nested_variations_and_exact_parent_positions() -> None:
    records = parse_repertoire_pgn(PGN)
    assert len(records) == 1
    title, pgn, tree = records[0]
    assert title == "Nested repertoire"
    assert len(tree.nodes) == 9
    root = tree.nodes[0]
    assert [tree.nodes[index].uci for index in root.children] == ["e2e4", "d2d4"]
    e4 = tree.nodes[root.children[0]]
    d4 = tree.nodes[root.children[1]]
    assert [tree.nodes[index].uci for index in e4.children] == ["e7e5", "c7c5"]
    assert [tree.nodes[index].uci for index in d4.children] == ["d7d5", "g8f6"]
    for node in tree.nodes[1:]:
        assert node.parent is not None and node.uci is not None
        before = chess.Board(tree.nodes[node.parent].fen)
        before.push_uci(node.uci)
        assert before.fen() == node.fen
    assert parse_repertoire_pgn(pgn)[0][2] == tree


def test_preserves_separate_records_and_roots() -> None:
    second = (
        '[Event "Black setup"]\n[SetUp "1"]\n'
        '[FEN "7k/8/8/8/8/8/4p3/K7 b - - 0 23"]\n\n'
        "23... e1=Q+ (23... e1=N) *\n"
    )
    records = parse_repertoire_pgn(PGN + "\n" + second)
    assert len(records) == 2
    assert records[0][2].nodes[0].fen == chess.STARTING_FEN
    assert records[1][2].nodes[0].fen.endswith("b - - 0 23")
    assert [node.uci for node in records[1][2].nodes[1:]] == ["e2e1q", "e2e1n"]


@pytest.mark.parametrize(
    "pgn",
    [
        "",
        "garbage",
        "1. e4 e5 2. e8 *",
        "1. e4 nonsense e5 *",
        "1. e4 -- *",
        "1. e4 (1. d4 *",
        "1. e4 ) *",
        '[Variant "Chess960"]\n\n1. e4 *',
        '[Event "No moves"]\n\n*',
        "1. e4 (1. e4) *",
        "1. e4 * 1... e5",
        '[SetUp "1"]\n[FEN "8/8/8/8/8/8/8/8 w - - 0 1"]\n\n1. e4 *',
    ],
)
def test_rejects_invalid_or_unsupported_content_without_truncating(pgn: str) -> None:
    with pytest.raises(ValueError):
        parse_repertoire_pgn(pgn)


def test_bounds_bytes_records_depth_nodes_and_line_length(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="256 KiB"):
        parse_repertoire_pgn("{" + "x" * (256 * 1024) + "}")
    with pytest.raises(ValueError, match="16 PGN"):
        parse_repertoire_pgn(("1. e4 *\n\n") * 17)
    with pytest.raises(ValueError, match="32 levels"):
        parse_repertoire_pgn("(" * 33 + ")" * 33)
    monkeypatch.setattr("scan64.learning.repertoire.MAX_NODES", 2)
    with pytest.raises(ValueError, match="4096"):
        parse_repertoire_pgn("1. e4 e5 *")
    monkeypatch.setattr("scan64.learning.repertoire.MAX_NODES", 4096)
    monkeypatch.setattr("scan64.learning.repertoire.MAX_PLY", 1)
    with pytest.raises(ValueError, match="256 plies"):
        parse_repertoire_pgn("1. e4 e5 *")


def test_verifies_both_sides_from_saved_position_not_client_answer() -> None:
    title, pgn, tree = parse_repertoire_pgn(PGN)[0]
    repertoire = Repertoire(player_id="owner", title=title, pgn=pgn, tree=tree.model_dump())
    assert verify_repertoire_move(repertoire, 0, "e2e4")
    assert verify_repertoire_move(repertoire, 0, "d2d4")
    e4_id = tree.nodes[0].children[0]
    assert verify_repertoire_move(repertoire, e4_id, "c7c5")
    assert not verify_repertoire_move(repertoire, e4_id, "g8f6")
    with pytest.raises(ValueError, match="legal UCI"):
        verify_repertoire_move(repertoire, e4_id, "e2e4")
    with pytest.raises(ValueError):
        verify_repertoire_move(repertoire, 999, "e2e4")
    with pytest.raises(ValueError):
        verify_repertoire_move(repertoire, tree.nodes[-1].id, "a7a6")


def test_repertoire_fsrs_cannot_claim_app_timing_or_hints() -> None:
    from datetime import UTC, datetime

    from scan64.learning.scheduling.spaced_repetition import (
        ReviewSchedule,
        ScheduleReviewObservation,
        review_rating,
    )

    now = datetime.now(UTC)
    schedule = ReviewSchedule(player_id="owner", item_id="repertoire:exact:0", next_review_at=now)
    observation = schedule.update(
        True,
        now,
        hints_used=None,
        elapsed_ms=None,
        source_kind="repertoire",
        source_id="attempt",
    )
    assert observation.input_provenance == "server_recorded"
    assert observation.rating == 3
    with pytest.raises(ValueError, match="Repertoire hints and solve time"):
        review_rating(success=True, hints_used=0, elapsed_ms=1, source_kind="repertoire")
    with pytest.raises(ValueError, match="provenance"):
        ScheduleReviewObservation.model_validate(
            {
                **observation.model_dump(),
                "input_provenance": "app_reported",
            }
        )
