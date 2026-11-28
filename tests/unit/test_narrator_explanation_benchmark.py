"""The explanation quality benchmark (#403): metric, rubric join, stored results, page."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.narrator.explanation import judge, metric, positions, run, summarise
from benchmarks.narrator.explanation.__main__ import main
from benchmarks.narrator.voice import pipeline
from scan64.explanations.sentence_check import sentence_problems
from scan64.explanations.themes import ThemeFacts, ThemePiece, TurningPointFacts

PAGE = Path(__file__).parents[2] / "docs" / "evaluation" / "explanation-quality.md"
SET = positions.load()


def _theme(key: str, **extra: Any) -> ThemeFacts:
    return ThemeFacts(
        key=key,
        signal="s",
        threshold=0,
        measured=(0,),
        held=(0,),
        move_from=1,
        move_to=3,
        **extra,
    )


KING = _theme(
    "unsafe_king",
    pieces=(
        ThemePiece(owner="learner", piece="king", square="g1"),
        ThemePiece(owner="opponent", piece="rook", square="f2"),
    ),
    files=("f",),
)
PAWNS = _theme("weak_pawns", squares=("b7",), weaknesses=("backward",))
PASSIVE = _theme("passive_pieces", pieces=(ThemePiece(owner="learner", piece="rook", square="a8"),))


@pytest.mark.parametrize(
    ("theme", "text", "stated"),
    [
        (KING, "The king on g1 shivered: the f-file lay open.", True),
        (KING, "The king on g1 felt the rook on f2 nearby.", True),
        (KING, "The king on g1 shivered.", False),
        (KING, "The king on h1 shivered: the f-file lay open.", False),
        (PAWNS, "Black's pawns were weak: a backward pawn on b7!", True),
        (PAWNS, "Black's pawn on b7 was a lone wanderer.", False),
        (PASSIVE, "The rook on a8 had nowhere to go.", True),
        (PASSIVE, "The rook on d8 had nowhere to go.", False),
    ],
)
def test_the_metric_needs_the_details_of_the_theme(
    theme: ThemeFacts, text: str, stated: bool
) -> None:
    assert metric.states_theme(theme, text) is stated


def test_the_turning_point_needs_the_move_and_the_turn() -> None:
    point = TurningPointFacts(
        ply=27,
        move_number=14,
        side="white",
        san="Qd2",
        uci="d1d2",
        evaluation_before_cp=0,
        evaluation_after_cp=-200,
        evaluation_end_cp=-500,
        is_mistake=True,
    )
    assert metric.states_turn(point, "With 14.Qd2 the game began to tilt against White.")
    assert not metric.states_turn(point, "White plays 14.Qd2.")
    assert not metric.states_turn(point, "The game began to tilt with 14.Qd21.")


def test_plain_facts_score_full_marks_and_a_story_without_its_cause_scores_none() -> None:
    rows = [run.explain(None, run.PLAIN, p) for p in SET]
    rates = metric.rates(rows)
    assert rates["theme_stated"] == rates["themed"] > 0
    assert rates["turn_stated"] == rates["turned"] > 0
    for row in rows:
        row["sentences"] = [s for s in row["sentences"] if s["role"] not in ("the_cause",)]
        row["sentences"] = [s for s in row["sentences"] if s["role"] != "the_turn"]
    cut = metric.rates(rows)
    assert cut["turn_stated"] == 0
    assert cut["theme_stated"] <= 1  # one line repeats the king, its square and an attacker


def test_a_provider_error_is_counted_and_not_accepted() -> None:
    class Down:
        def chat(self, system: str, user: str) -> tuple[str, float, int, int]:
            raise OSError("down")

    row = run.explain(Down(), "Tal", SET[0])  # type: ignore[arg-type]
    rates = metric.rates([row])
    assert (rates["records"], rates["accepted"], rates["theme_stated"]) == (1, 0, 0)


def test_every_card_example_passes_the_sentence_check() -> None:
    for voice in ("Tal", "Chernev"):
        card = run.cards(voice)
        for _, fact, text in card["CAUSE_EXAMPLES"]:
            assert sentence_problems(text, fact, (), {"g1": "white"}) == []


def test_the_join_refuses_a_missing_or_unasked_answer() -> None:
    key = {"q1": {"asks": {"root_cause_named": True, "turning_point_named": False, "order": True}}}
    good = {
        "id": "q1",
        "faithful": True,
        "jargon": False,
        "lively": 2,
        "clear": 3,
        "root_cause_named": True,
        "turning_point_named": None,
        "order": True,
    }
    assert judge.join(key, [good])
    for change in ({"root_cause_named": None}, {"turning_point_named": True}, {"order": "yes"}):
        with pytest.raises(ValueError, match="must be"):
            judge.join(key, [copy.deepcopy(good) | change])


def test_the_stored_blind_set_rebuilds_from_the_stored_records() -> None:
    folder = summarise.RESULTS
    items, key = judge.build(summarise.records(), 403)
    assert items == json.loads((folder / "blind-items.json").read_text(encoding="utf-8"))
    assert key == json.loads((folder / "blind-key.json").read_text(encoding="utf-8"))
    assert all(set(item) == {"id", "position", "facts", "comment"} for item in items)


def test_every_stored_record_carries_its_provenance() -> None:
    for row in summarise.records():
        assert row["meta"]["dirty"] is False
        assert row["meta"]["prompt_sha256"]
        if row["style"] != run.PLAIN:
            assert row["meta"]["model_digest"] and row["meta"]["think"] is False


def test_the_page_holds_the_tables_of_the_stored_records() -> None:
    page = PAGE.read_text(encoding="utf-8")
    assert summarise.write_doc(page) == page


def test_a_run_refuses_a_dirty_tree_and_the_results_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pipeline, "tree_state", lambda exclude: (True, "x"))
    with pytest.raises(SystemExit, match="uncommitted"):
        run.run(run.PLAIN, None, 1)
    target = summarise.RESULTS / "new.jsonl"
    with pytest.raises(SystemExit, match="immutable"):
        main(["run", "--voice", "plain", "--out", str(target)])
    judge_dir = tmp_path / "judge"
    source = str(summarise.RESULTS / "plain.jsonl")
    with pytest.raises(SystemExit, match="would see the key"):
        main(
            ["blind", source, "--seed", "1", "--out", str(judge_dir)]
            + ["--key-out", str(judge_dir / "key.json")]
        )


def test_the_stored_measurements_are_unchanged() -> None:
    digest = hashlib.sha256()
    for path in sorted(summarise.RESULTS.iterdir()):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    assert digest.hexdigest() == (
        "7f274ed8e823884944b7ef7ea0bddf9b59b8710cfad98131afe1c2b7ed29c4bf"
    )
