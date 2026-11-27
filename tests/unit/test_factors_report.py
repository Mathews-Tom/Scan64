"""The factor report: structure, sums, symmetry and runtime limits."""

from __future__ import annotations

import ast
import gzip
import json
import time
from pathlib import Path

import chess
import pytest

from scan64.explanations.factors import GROUPS, PARTS, TERM_NAMES, FactorReport, evaluate_factors

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "factors"
PACKAGE = ROOT / "src" / "scan64" / "explanations" / "factors"


def _positions() -> list[dict[str, str]]:
    positions: list[dict[str, str]] = json.loads((FIXTURES / "positions.json").read_text())[
        "positions"
    ]
    return positions


def test_report_matches_the_guide_main_evaluation() -> None:
    reference = json.loads(gzip.decompress((FIXTURES / "seg_reference.json.gz").read_bytes()))
    for r in reference["results"][:60]:
        report = evaluate_factors(r["fen"])
        assert report.evaluation == r["white"]["Main evaluation"]
        assert report.middle_game == r["white"]["Middle game evaluation"]
        assert report.end_game == r["white"]["End game evaluation"]
        assert report.phase == r["white"]["Phase"]
        assert report.scale_factor == r["white"]["Scale factor"]


@pytest.mark.parametrize("position", _positions()[::9], ids=lambda p: p["id"])
def test_groups_and_contributions_add_up(position: dict[str, str]) -> None:
    report = evaluate_factors(position["fen"])
    assert tuple(report.groups) == GROUPS
    assert [(c.name, c.group) for c in report.contributions] == list(PARTS)
    assert sum(g.net_mg for g in report.groups.values()) == report.middle_game
    assert sum(g.net_eg for g in report.groups.values()) == report.end_game
    for group, value in report.groups.items():
        members = [c for c in report.contributions if c.group == group]
        assert value.net_mg == sum(c.net_mg for c in members)
        assert value.net_eg == sum(c.net_eg for c in members)
        if value.white_mg is not None:
            assert value.net_mg == value.white_mg - (value.black_mg or 0)
    for c in report.contributions:
        assert c.name in TERM_NAMES
    tapered = sum(g.tapered for g in report.groups.values())
    unrounded = (
        report.middle_game * report.phase
        + report.end_game * report.scale_factor / 64 * (128 - report.phase)
    ) / 128
    assert tapered == pytest.approx(unrounded)


@pytest.mark.parametrize("position", _positions()[::7], ids=lambda p: p["id"])
def test_mirrored_position_negates_the_evaluation(position: dict[str, str]) -> None:
    board = chess.Board(position["fen"])
    report = evaluate_factors(board)
    mirrored = evaluate_factors(board.mirror())
    assert mirrored.evaluation == -report.evaluation
    for name, value in report.terms.items():
        assert mirrored.terms[name].white == value.black, name
        assert mirrored.terms[name].black == value.white, name
    for group, group_value in report.groups.items():
        assert mirrored.groups[group].net_mg == -group_value.net_mg
        assert mirrored.groups[group].net_eg == -group_value.net_eg


def test_report_serialises_to_json() -> None:
    report = evaluate_factors(chess.Board())
    data = json.loads(json.dumps(report.to_dict()))
    assert data["evaluation"] == 28
    assert set(data["terms"]) == set(TERM_NAMES)
    assert FactorReport.to_pawns(206) == 1.0


def test_board_without_both_kings_is_rejected() -> None:
    with pytest.raises(ValueError):
        evaluate_factors("8/8/8/8/8/8/8/4K3 w - - 0 1")


def test_package_needs_no_engine_or_network() -> None:
    banned = ("subprocess", "socket", "urllib", "http", "httpx", "requests", "asyncio")
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in banned, (path.name, name)
                assert name != "chess.engine", path.name


def test_mean_time_per_position_is_bounded() -> None:
    """Measured mean on linux-5090 is in docs/narrator-pipeline.md; this bound is for slower CI."""
    fens = [p["fen"] for p in _positions()]
    start = time.perf_counter()
    for fen in fens:
        evaluate_factors(fen)
    mean_ms = (time.perf_counter() - start) * 1000 / len(fens)
    assert mean_ms < 50
