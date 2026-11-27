"""The committed Stockfish 11 comparison is current and every gap has a cause."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.factors.sf11_compare import CAUSES, COMPARISON, REPORT, TOLERANCE, build, render


def test_comparison_fixture_is_current() -> None:
    data = build()
    assert data == json.loads(COMPARISON.read_text())
    assert render(data) == REPORT.read_text()


def test_every_difference_over_tolerance_has_a_known_cause() -> None:
    data = json.loads(COMPARISON.read_text())
    assert data["compared_positions"] >= 200
    over = [
        (r["id"], r["group"])
        for r in data["rows"]
        if abs(r["diff_mg"]) > TOLERANCE or abs(r["diff_eg"]) > TOLERANCE
    ]
    assert over == [(r["id"], r["group"]) for r in data["over_tolerance"]]
    for row in data["over_tolerance"]:
        assert row["causes"], row["id"]
        assert set(row["causes"]) <= set(CAUSES), row["causes"]


def test_skipped_positions_have_a_reason() -> None:
    data = json.loads(COMPARISON.read_text())
    assert {s["status"] for s in data["skipped"]} <= {"in_check", "lazy", "specialized"}
    assert Path(REPORT).read_text().count("| ") > len(data["over_tolerance"])
