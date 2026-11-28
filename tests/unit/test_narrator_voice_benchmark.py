# ruff: noqa: E501
# The published #462 table below is copied verbatim; its rows are longer than 100.
"""The narrator voice benchmark (#458): verified facts, checked examples, blind sets, tables."""

import json
from typing import Any

import pytest

from benchmarks.narrator.voice import data, pipeline, verify
from benchmarks.narrator.voice.blind import SETS, build, join
from benchmarks.narrator.voice.check import sentence_ok, violations
from benchmarks.narrator.voice.pipeline import REPO_ROOT
from benchmarks.narrator.voice.summarise import (
    doc_tables,
    heldout_positions,
    models_table,
    summarise,
)

# --- Facts: every fact of every run is checked with python-chess -------------------------------


@pytest.mark.parametrize(("test", "fact"), verify.all_facts())
def test_every_fact_names_its_claims(test: str, fact: str) -> None:
    claims = verify.FACT_CLAIMS.get(fact)
    assert claims, f"{test}: no python-chess check for {fact!r}"
    position = test.split("-")[0].lower()
    assert all(c.startswith(position + ".") for c in claims), (test, claims)


@pytest.mark.parametrize("claim", sorted(verify.CLAIMS))
def test_every_claim_holds_on_the_board(claim: str) -> None:
    assert verify.CLAIMS[claim](), claim


def test_no_claim_is_left_unused() -> None:
    used = {c for claims in verify.FACT_CLAIMS.values() for c in claims}
    assert used == set(verify.CLAIMS)


def test_a_false_fact_fails_its_check() -> None:
    """The checks can fail: the knight on c7 does not attack a8 after 1.Kd2."""
    after_kd2 = verify.board("P4-fork", "Kd2")
    assert not verify.attacks(after_kd2, "d5", "a8")
    assert not verify.board("H3-stalemate", "Qf8#").is_stalemate()


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


def test_join_refuses_a_missing_judgement() -> None:
    with pytest.raises(ValueError, match="1 items have no judgement"):
        join({"x001": {"arm": "a"}, "x002": {"arm": "b"}}, [{"id": "x001", "faithful": True}])


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
    assert models_table() == PUBLISHED_MODELS_462


def test_heldout_positions_match_the_published_462_table() -> None:
    lines = {line.split(" | ")[0].strip("| "): line for line in heldout_positions().splitlines()}
    for cell, line in PUBLISHED_HELDOUT_POSITIONS_462.items():
        assert lines[cell] == line


def test_the_voice_page_shows_the_summarise_tables() -> None:
    page = (REPO_ROOT / "docs" / "evaluation" / "narrator-voice.md").read_text(encoding="utf-8")
    assert doc_tables(page) == summarise().strip("\n"), "run: summarise --doc <page>"


# --- Records carry their provenance ----------------------------------------------------------


def test_meta_names_commit_model_digest_server_and_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = {
        "/api/tags": {"models": [{"name": "m:latest", "digest": "abc123"}]},
        "/api/version": {"version": "0.40.18"},
    }
    monkeypatch.setattr(pipeline, "_get", lambda url: replies[url.removeprefix("http://s")])
    monkeypatch.setattr(pipeline, "_git", lambda *a: "deadbeef" if a[0] == "rev-parse" else "")
    meta = pipeline.Client("http://s", "m", {"temperature": 1.0}).meta("run9")
    assert meta == {
        "commit": "deadbeef",
        "dirty": False,
        "model": "m",
        "model_digest": "abc123",
        "ollama_version": "0.40.18",
        "think": False,
        "options": {"num_ctx": 4096, "temperature": 1.0},
        "prompt_version": "run9",
    }
    with pytest.raises(SystemExit, match="not on the Ollama server"):
        pipeline.Client("http://s", "other").meta("run9")


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
