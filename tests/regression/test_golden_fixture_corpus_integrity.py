import json
from pathlib import Path

FIXTURES_DIR = Path("benchmarks/fixtures")
CORPUS_FILE = FIXTURES_DIR / "golden_corpus.json"
REVIEW_FILE = FIXTURES_DIR / "REVIEW.md"


def test_golden_fixture_corpus_integrity():
    assert CORPUS_FILE.exists(), f"Corpus file missing: {CORPUS_FILE}"
    assert REVIEW_FILE.exists(), f"Review file missing: {REVIEW_FILE}"

    with open(CORPUS_FILE, encoding="utf-8") as f:
        corpus = json.load(f)

    assert len(corpus) >= 50, f"Expected at least 50 fixtures, found {len(corpus)}"

    has_false_positive_trap = False
    has_quiet_move_case = False
    has_multiple_acceptable = False
    for fixture in corpus:
        assert "fen" in fixture, f"Missing fen in fixture: {fixture}"
        assert "expected_label" in fixture, f"Missing expected_label in fixture: {fixture}"
        assert "tags" in fixture, f"Missing tags in fixture: {fixture}"

        if "false_positive_trap" in fixture["tags"]:
            has_false_positive_trap = True
        if "quiet_move_case" in fixture["tags"]:
            has_quiet_move_case = True

        if "multiple_acceptable_moves" in fixture["tags"]:
            has_multiple_acceptable = True

    assert has_false_positive_trap, "Corpus missing at least one 'false_positive_trap' case"
    assert has_quiet_move_case, "Corpus missing at least one 'quiet_move_case'"
    assert has_multiple_acceptable, "Corpus missing at least one 'multiple_acceptable_moves' case"

    with open(REVIEW_FILE, encoding="utf-8") as f:
        review_content = f.read()

    assert "sign-off" in review_content.lower() or "signed off" in review_content.lower(), (
        "REVIEW.md must contain explicit reviewer sign-off"
    )
