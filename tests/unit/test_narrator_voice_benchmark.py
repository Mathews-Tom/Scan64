# ruff: noqa: E501
# The published #462 table below is copied verbatim; its rows are longer than 100.
"""The narrator voice benchmark (#458): verified facts, checked examples, blind sets, tables."""

import hashlib
import json
from pathlib import Path
from typing import Any

import chess
import pytest

from benchmarks.narrator.voice import data, pipeline, summarise, verify
from benchmarks.narrator.voice.__main__ import main
from benchmarks.narrator.voice.blind import RESULTS, SETS, build, join
from benchmarks.narrator.voice.check import sentence_ok, violations
from benchmarks.narrator.voice.pipeline import REPO_ROOT
from benchmarks.narrator.voice.summarise import doc_tables, heldout_positions, models_table

# --- Facts: every fact of every run is checked with python-chess -------------------------------


@pytest.mark.parametrize(("test", "fact", "clause"), verify.all_clauses())
def test_every_clause_of_every_fact_has_a_claim(test: str, fact: str, clause: str) -> None:
    claims = verify.claims_of(test, clause)
    assert claims, f"{test}: no python-chess check for {clause!r} in {fact!r}"
    position = test.split("-")[0].lower()
    assert all(c in verify.CLAIMS and c.startswith(position + ".") for c in claims), claims


def test_a_fact_with_an_unchecked_clause_fails() -> None:
    fact = "White played 1.Kd2, and the king feels safe."
    assert [verify.claims_of("P4-fork", c) for c in verify.clauses(fact)] == [["p4.kd2-legal"], []]


def test_no_clause_key_is_stale() -> None:
    keys = {(p, c) for p, mapping in verify.CLAUSE_CLAIMS.items() for c in mapping}
    found = {(t.split("-")[0], c) for t, _, c in verify.all_clauses()}
    assert keys == found


@pytest.mark.parametrize("claim", sorted(verify.CLAIMS))
def test_every_claim_holds_on_the_board(claim: str) -> None:
    assert verify.CLAIMS[claim](), claim


def test_no_claim_is_left_unused() -> None:
    used = {c for mapping in verify.CLAUSE_CLAIMS.values() for cs in mapping.values() for c in cs}
    assert used == set(verify.CLAIMS)


def test_a_false_fact_fails_its_check() -> None:
    """The checks can fail: the knight on c7 does not attack a8 after 1.Kd2."""
    after_kd2 = verify.board("P4-fork", "Kd2")
    assert not verify.attacks(after_kd2, "d5", "a8")
    assert not verify.board("H3-stalemate", "Qf8#").is_stalemate()


@pytest.mark.parametrize("test", sorted(verify.all_fens()))
def test_every_stored_position_is_legal(test: str) -> None:
    """H2 is the one exception: the run 9 records came from its illegal FEN (#500)."""
    board = chess.Board(verify.all_fens()[test])
    if test in data.ILLEGAL_POSITIONS:
        assert board.status() == chess.STATUS_OPPOSITE_CHECK
    else:
        assert board.is_valid(), board.status()


def test_the_legal_h2_is_a_skewer() -> None:
    """Before 1.Bf3+ the king is not attacked; after it, the rook stands behind the king."""
    before, after = verify.board("H2b-skewer"), verify.board("H2b-skewer", "Bf3+")
    assert not before.is_attacked_by(chess.WHITE, chess.D5)
    assert after.checkers() == chess.SquareSet([chess.F3])
    assert chess.D5 in chess.SquareSet.between(chess.F3, chess.B7)
    without_king = after.copy()
    without_king.remove_piece_at(chess.D5)
    assert chess.B7 in without_king.attacks(chess.F3)


# --- Examples pass the same checks as model output ---------------------------------------------


def sentence_examples(sets: dict[str, list[list[str]]]) -> list[tuple[str, str, str]]:
    out = []
    for style, groups in sets.items():
        for facts, sentences in zip(data.EXAMPLE_FACTS_RUN5, groups, strict=True):
            for (_, fact), sentence in zip(facts, sentences, strict=True):
                out.append((style, fact, sentence))
    return out


@pytest.mark.parametrize(("style", "fact", "sentence"), sentence_examples(data.SENTENCES_RUN6))
def test_runs_6_to_9_examples_pass_the_sentence_check(style: str, fact: str, sentence: str) -> None:
    assert sentence_ok(sentence, fact) == [], (style, sentence)


def test_run_7_chernev_examples_have_one_known_failure() -> None:
    """Run 7 ran Tal only, so this example never reached a stored prompt; it stays as written."""
    failures = [
        (sentence, sentence_ok(sentence, fact))
        for _, fact, sentence in sentence_examples({"Chernev": data.SENTENCES_CHERNEV_RUN7})
        if sentence_ok(sentence, fact)
    ]
    assert failures == [
        ("Notice that the e-file lies wide open, waiting for a rook!", ["piece rook"])
    ]


def whole_examples() -> list[tuple[str, str, str, list[str]]]:
    out = []
    for style, card in data.STYLES_V1.items():
        for facts, text in zip(data.EXAMPLE_FACTS_V1, card["examples"], strict=True):
            out.append(("1-3", style, text, facts))
    for style, card in data.STYLES_RUN4.items():
        for facts, text in zip(data.EXAMPLE_FACTS_RUN4, card["examples"], strict=True):
            out.append(("4", style, text, [f for _, f in facts]))
    for style, examples in data.EXAMPLES_RUN5.items():
        for facts, text in zip(data.EXAMPLE_FACTS_RUN5, examples, strict=True):
            out.append(("5", style, text, [f for _, f in facts]))
    return out


@pytest.mark.parametrize(("run", "style", "text", "facts"), whole_examples())
def test_runs_1_to_5_examples_pass_the_word_check(
    run: str, style: str, text: str, facts: list[str]
) -> None:
    assert violations(text, facts) == [], (run, style, text)


def test_the_sentence_check_rejects_a_new_square() -> None:
    assert sentence_ok("White plays 1.e3 and eyes h5.", "White played 1.e3.") == ["square h5"]


# --- Blind sets ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SETS))
def test_stored_blind_sets_rebuild_exactly(name: str) -> None:
    stored = SETS[name]
    items, key = stored.build()
    assert items == [json.loads(p.read_text()) for p in stored.item_paths()]
    assert key == json.loads(stored.key_path().read_text())
    assert len(stored.joined()) == len(key)


def rows(arm: str, count: int) -> list[dict[str, Any]]:
    return [{"arm": arm, "test": "P4-fork", "text": f"{arm} {i}", "n": i} for i in range(count)]


def test_halves_keep_every_arm_in_equal_share() -> None:
    sample = rows("a", 10) + rows("b", 6) + rows("c", 7)
    halves, key = build(
        sample, seed=1, prefix="x", facts="run5", key_fields=("arm", "n"), halves_by="arm"
    )
    for arm, count in (("a", 10), ("b", 6), ("c", 7)):
        first = sum(key[i["id"]]["arm"] == arm for i in halves[0])
        second = sum(key[i["id"]]["arm"] == arm for i in halves[1])
        assert (first, second) == (count // 2, count - count // 2)
    ids = [i["id"] for half in halves for i in half]
    assert len(ids) == len(set(ids)) == len(key) == len(sample)
    shown = {i["comment"] for half in halves for i in half}
    assert shown == {r["text"] for r in sample}
    assert all(set(i) == {"id", "position", "facts", "comment"} for h in halves for i in h)


def test_stored_model_halves_hold_25_comments_per_model() -> None:
    joined = SETS["run8-models"].joined()
    for model in {r["model"] for r in joined}:
        counts = [
            sum(r["model"] == model and r["id"].startswith(f"m{h}") for r in joined) for h in (0, 1)
        ]
        assert counts == [25, 25], model


def verdict(item_id: str, **change: Any) -> dict[str, Any]:
    good = {"id": item_id, "faithful": True, "error": "", "lively": 2, "clear": 3, "jargon": False}
    return good | change


KEY = {"x001": {"arm": "a"}, "x002": {"arm": "b"}}


def test_join_returns_each_item_with_its_key() -> None:
    joined = join(KEY, [verdict("x002"), verdict("x001")])
    assert [(r["id"], r["arm"], r["lively"]) for r in joined] == [
        ("x001", "a", 2),
        ("x002", "b", 2),
    ]


@pytest.mark.parametrize(
    ("judgements", "message"),
    [
        ({"x001": verdict("x001")}, "must be a list of objects"),
        (["x001", "x002"], "must be a list of objects"),
        ([verdict(["x001"]), verdict("x002")], "ids must be strings"),
        ([verdict("x001")], "1 items have no judgement"),
        ([verdict("x001"), verdict("x002"), verdict("x003")], "1 judgements have no item"),
        ([verdict("x001"), verdict("x001"), verdict("x002")], "duplicate judgement ids"),
        ([verdict("x001", faithful="yes"), verdict("x002")], "faithful must be true or false"),
        ([verdict("x001", jargon=0), verdict("x002")], "jargon must be true or false"),
        ([verdict("x001", lively=4), verdict("x002")], "lively must be 1, 2 or 3"),
        ([verdict("x001", lively=True), verdict("x002")], "lively must be 1, 2 or 3"),
        ([verdict("x001", clear="3"), verdict("x002")], "clear must be 1, 2 or 3"),
        ([verdict("x001", clear=0), verdict("x002")], "clear must be 1, 2 or 3"),
    ],
)
def test_join_refuses_a_bad_judgement_file(judgements: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        join(KEY, judgements)


def test_cli_blind_writes_the_key_outside_the_judges_folder(tmp_path: Path) -> None:
    records = [str(RESULTS / "run9" / f"run9-{c}.jsonl") for c in ("A-gemma", "C-praxis-t0")]
    args = ["blind", *records, "--facts", "heldout", "--seed", "1", "--prefix", "x"]
    main([*args, "--out", str(tmp_path / "judge"), "--key-out", str(tmp_path / "key.json")])
    assert [p.name for p in (tmp_path / "judge").iterdir()] == ["blind-items.json"]
    key = json.loads((tmp_path / "key.json").read_text())
    assert len(key) == 100
    assert set(next(iter(key.values()))) == {"run", "test", "model", "own", "n"}
    with pytest.raises(SystemExit, match="the judge would see the key"):
        main([*args, "--out", str(tmp_path / "judge"), "--key-out", str(tmp_path / "judge" / "k")])


def test_join_refuses_a_key_that_is_not_a_mapping_of_fields() -> None:
    with pytest.raises(ValueError, match="the key must map each item id"):
        join({"x001": "a"}, [verdict("x001")])


def test_a_failed_start_leaves_no_file_and_a_retry_needs_no_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "run9.jsonl"
    args = ["run", "9", "--samples", "1", "--tests", "H3-stalemate", "--url", "http://s"]
    with pytest.raises(SystemExit, match="has no position"):
        main([*args[:5], "H9-nothing", *args[6:], "--model", "m", "--out", str(out)])
    assert not out.exists()
    fake_server(monkeypatch)
    with pytest.raises(SystemExit, match="not on the Ollama server"):
        main([*args, "--model", "missing", "--out", str(out)])
    assert not out.exists()

    def chat(self: pipeline.Client, system: str, user: str) -> tuple[str, float, int, int]:
        return "White plays 1.Qf7.", 0.1, 10, 5

    monkeypatch.setattr(pipeline.Client, "chat", chat)
    main([*args, "--model", "m", "--out", str(out)])
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(records) == 1 and records[0]["meta"]["model_digest"] == "abc123"


def test_run_checks_styles_rule_and_samples_before_any_call() -> None:
    client = pipeline.Client("http://never")
    for kwargs, message in (
        ({"styles": ["Nunn"]}, "has no style Nunn"),
        ({"own_words": True}, "belongs to run 9 only"),
        ({"samples": 0}, "must be 1 or more"),
    ):
        with pytest.raises(SystemExit, match=message):
            pipeline.run(client, 8, **kwargs)


def test_cli_writes_nothing_into_results(tmp_path: Path) -> None:
    stored = RESULTS / "run8" / "run8-tal.jsonl"
    before = stored.read_bytes()
    with pytest.raises(SystemExit, match="stored measurements are immutable"):
        main(["run", "8", "--out", str(stored), "--force"])
    with pytest.raises(SystemExit, match="stored measurements are immutable"):
        main(["run", "8", "--out", str(RESULTS / "run8" / "new.jsonl")])
    with pytest.raises(SystemExit, match="stored measurements are immutable"):
        main(
            [
                "blind",
                "--stored",
                "run8",
                "--out",
                str(RESULTS / "x"),
                "--key-out",
                str(tmp_path / "k"),
            ]
        )
    assert stored.read_bytes() == before
    assert not (RESULTS / "run8" / "new.jsonl").exists()


# --- Tables are rebuilt from the records, as published ---------------------------------------

PUBLISHED_MODELS_462 = """\
| Model | GPU GiB | Faithful (±95%) | Judge 1 / Judge 2 | Faithful and lively | Lively | Clear | Fallback sentences | p95 s |
|---|---|---|---|---|---|---|---|---|
| gemma3 270M | 0.30 | 72% ±12 | 80% / 64% | 0% | 1.00 | 2.48 | 70.0% | 7.02 |
| gemma3 1B | 0.82 | 12% ±9 | 24% / 0% | 12% | 2.00 | 1.22 | 8.8% | 0.36 |
| praxis-2b (chess LoRA) | 1.30 | 38% ±13 | 48% / 28% | 12% | 1.50 | 2.20 | 35.3% | 0.60 |
| second-2b (chess SFT) | 1.30 | 20% ±11 | 16% / 24% | 16% | 2.00 | 1.96 | 12.4% | 1.08 |
| llama3.2 1B | 1.41 | 30% ±13 | 36% / 24% | 4% | 1.24 | 1.56 | 21.8% | 69.49 |
| qwen3 1.7B | 1.59 | 34% ±13 | 24% / 44% | 22% | 1.56 | 2.30 | 2.9% | 0.31 |
| qwen3.5 2B | 2.20 | 12% ±9 | 12% / 12% | 10% | 2.54 | 1.58 | 10.0% | 1.16 |
| gemma4-e4b Q3_K_M (current default) | 2.43 | 86% ±10 | 100% / 72% | 86% | 2.14 | 2.86 | 5.6% | 0.74 |
| praxis-4b (chess LoRA) | 2.76 | 100% | 100% / 100% | 68% | 1.88 | 3.00 | 5.9% | 0.96 |
| gemma4-e4b Q4_K_S | 2.84 | 98% ±4 | 100% / 96% | 98% | 2.08 | 2.96 | 2.4% | 0.69 |
| qwen3.5 4B | 2.92 | 58% ±14 | 68% / 48% | 58% | 2.70 | 2.40 | 1.8% | 1.64 |
| caissa-m1-8b (reference, above budget) | 4.43 | 20% ±11 | 20% / 20% | 20% | 2.64 | 2.04 | 16.5% | 1.06 |"""

PUBLISHED_HELDOUT_POSITIONS_462 = {
    "A": "| A | 10/10 | 10/10 | 9/10 | 10/10 | 9/10 |",
    "B": "| B | 9/10 | 10/10 | 10/10 | 10/10 | 10/10 |",
    "D": "| D | 7/10 | 7/10 | 8/10 | 3/10 | 6/10 |",
    "E": "| E | 8/10 | 5/10 | 7/10 | 1/10 | 4/10 |",
}


def test_model_table_matches_the_published_462_table() -> None:
    """The one change: the label "(current default)" was dropped after #462 chose Q4_K_S."""
    assert models_table() == PUBLISHED_MODELS_462.replace(" (current default)", "")


def test_heldout_positions_match_the_published_462_table() -> None:
    lines = {line.split(" | ")[0].strip("| "): line for line in heldout_positions().splitlines()}
    for cell, line in PUBLISHED_HELDOUT_POSITIONS_462.items():
        assert lines[cell] == line


def test_the_voice_page_shows_the_summarise_tables() -> None:
    page = (REPO_ROOT / "docs" / "evaluation" / "narrator-voice.md").read_text(encoding="utf-8")
    assert doc_tables(page) == summarise.summarise().strip("\n"), "run: summarise --doc <page>"


def test_a_run_9_cell_with_mixed_settings_is_refused() -> None:
    records = [
        {"model": "m", "own": False, "options": {"temperature": 1.0}},
        {"model": "m", "own": True, "options": {"temperature": 1.0}},
    ]
    with pytest.raises(ValueError, match="mixes settings"):
        summarise.cell_settings("A-gemma", records)


def test_summary_lists_every_stored_file_with_its_sha256() -> None:
    table = summarise.stored_files()
    files = [p for p in RESULTS.rglob("*") if p.is_file() and p.name != "README.md"]
    for path in files:
        row = next(
            line
            for line in table.splitlines()
            if f"| {path.relative_to(RESULTS).as_posix()} |" in line
        )
        assert hashlib.sha256(path.read_bytes()).hexdigest() in row
    kinds = {line.split(" | ")[1] for line in table.splitlines()[2:]}
    assert {"records", "items", "key", "judgements"} <= kinds
    assert len(table.splitlines()) == 2 + len(files)


# --- Records carry their provenance ----------------------------------------------------------


def fake_server(monkeypatch: pytest.MonkeyPatch, status: str = "", diff: bytes = b"") -> None:
    replies = {
        "/api/tags": {"models": [{"name": "m:latest", "digest": "abc123"}]},
        "/api/version": {"version": "0.40.18"},
    }
    monkeypatch.setattr(pipeline, "_get", lambda url: replies[url.removeprefix("http://s")])
    answers = {"rev-parse": b"deadbeef", "status": status.encode(), "diff": diff, "ls-files": b""}
    monkeypatch.setattr(pipeline, "_git_bytes", lambda *a: answers[a[0]])


CONFIG = {
    "run": 9,
    "samples": 10,
    "styles": ["Tal"],
    "tests": None,
    "own_words": False,
    "options": {"temperature": 1.0},
    "url": "http://s",
}


def test_meta_names_code_prompt_data_model_server_and_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server(monkeypatch)
    meta = pipeline.Client("http://s", "m", {"temperature": 1.0}).meta(CONFIG, "f00d")
    assert meta == {
        "commit": "deadbeef",
        "dirty": False,
        "dirty_sha256": None,
        "model": "m",
        "model_digest": "abc123",
        "ollama_version": "0.40.18",
        "think": False,
        "options": {"num_ctx": 4096, "temperature": 1.0},
        "prompt_version": "run9",
        "prompt_sha256": "f00d",
        "config": CONFIG,
    }
    with pytest.raises(SystemExit, match="not on the Ollama server"):
        pipeline.Client("http://s", "other").meta(CONFIG, "f00d")


def test_the_output_file_does_not_make_the_tree_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    out = pipeline.REPO_ROOT / "new-run.jsonl"
    fake_server(monkeypatch, status="?? new-run.jsonl\n")
    assert pipeline.Client("http://s", "m").meta(CONFIG, "f00d", exclude=out)["dirty"] is False
    fake_server(
        monkeypatch, status="?? new-run.jsonl\n M benchmarks/narrator/voice/check.py\n", diff=b"d"
    )
    meta = pipeline.Client("http://s", "m").meta(CONFIG, "f00d", exclude=out)
    assert meta["dirty"] is True and len(meta["dirty_sha256"]) == 64


def test_a_modified_file_on_the_first_status_line_counts_as_dirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server(monkeypatch, status=" M benchmarks/narrator/voice/check.py\n", diff=b"d")
    out = pipeline.REPO_ROOT / "benchmarks/narrator/voice/check.py"
    assert pipeline.Client("http://s", "m").meta(CONFIG, "f00d", exclude=out)["dirty"] is False
    assert pipeline.Client("http://s", "m").meta(CONFIG, "f00d")["dirty"] is True


def test_the_prompt_hash_changes_with_the_prompt_data() -> None:
    hashes = {
        pipeline.sha256_json(pipeline.prompt_data(n, own))
        for n, own in ((8, False), (9, False), (9, True))
    }
    assert len(hashes) == 3
    assert pipeline.sha256_json(pipeline.prompt_data(6)) == pipeline.sha256_json(
        pipeline.prompt_data(6)
    )


class Scripted(pipeline.Client):
    """A model that answers from a list, to drive the per-fact pipeline without a server."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self.replies = replies

    def chat(self, system: str, user: str) -> tuple[str, float, int, int]:
        return self.replies.pop(0), 0.1, 100, 10


def test_a_sentence_with_a_new_square_is_retried_then_replaced_by_the_fact() -> None:
    facts = [("The move", "White played 1.Kd2.")]
    bad = "White's king strolls to d2, eyeing e3."
    client = Scripted([bad, bad, bad])
    record = pipeline.comment_per_fact(client, 8, "Tal", facts, "1.Kd2", data.SYSTEM_RUN6)
    assert (record["text"], record["calls"], record["fallback_sentences"]) == (
        "White played 1.Kd2.",
        3,
        1,
    )


def test_a_repeated_colour_word_is_rewritten_once() -> None:
    facts = [
        ("The move", "White played 1.Kd2."),
        ("What it misses", "Black's king on e8 is far away."),
    ]
    client = Scripted(
        [
            "A sleepy 1.Kd2!",
            "Black's sleepy king on e8 sits far away.",
            "Black's king on e8 dozes far away.",
        ]
    )
    record = pipeline.comment_per_fact(client, 8, "Tal", facts, "1.Kd2", data.SYSTEM_RUN6)
    assert record["rewrites"] == 1
    # The "!" after the played move is removed by code, as in every per-fact run.
    assert record["text"] == "A sleepy 1.Kd2. Black's king on e8 dozes far away."
