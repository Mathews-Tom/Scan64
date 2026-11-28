"""Rebuild every results table of #457 and #462 from the stored records and judgements.

Nothing here is typed by hand except the model behind each record file of runs 1-8 (those
records predate the ``meta`` field) and one GPU size that ``models-vram.txt`` lacks.
"""

import collections
import math
import re
from collections.abc import Callable, Iterable
from typing import Any

from benchmarks.narrator.voice import data
from benchmarks.narrator.voice.blind import RESULTS, SETS, read_jsonl
from benchmarks.narrator.voice.check import colour_words, tokens, violations
from benchmarks.narrator.voice.render import render

Row = dict[str, Any]

# Runs 1-8 predate the meta field; #457 names the model of each record file.
Q3 = "gemma4-e4b Q3_K_M"
MODEL_OF_SET = {
    "run4": Q3,
    "run5": Q3,
    "run5-12b": "gemma4-12b",
    "run6": Q3,
    "run7": Q3,
    "run8": Q3,
}
# Measured for run 8 with the same method as models-vram.txt, but not written to that file (#457).
GIB_Q3 = 2.43
LABELS = {
    "gemma3-270m": "gemma3 270M",
    "gemma3-1b": "gemma3 1B",
    "praxis-2b": "praxis-2b (chess LoRA)",
    "second-2b": "second-2b (chess SFT)",
    "llama3.2-1b": "llama3.2 1B",
    "qwen3-1.7b": "qwen3 1.7B",
    "qwen3.5-2b": "qwen3.5 2B",
    "gemma4-e4b-q3km": "gemma4-e4b Q3_K_M (current default)",
    "praxis-4b": "praxis-4b (chess LoRA)",
    "gemma4-e4b-q4ks": "gemma4-e4b Q4_K_S",
    "qwen3.5-4b": "qwen3.5 4B",
    "caissa-m1-8b": "caissa-m1-8b (reference, above budget)",
}


def pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%"


def with_interval(hits: int, n: int) -> str:
    """Share with its 95% interval (normal approximation); no interval at 0% or 100%."""
    p = hits / n
    half = 1.96 * math.sqrt(p * (1 - p) / n)
    return f"{100 * p:.0f}%" + (f" ±{100 * half:.0f}" if half else "")


def nearest_rank(values: Iterable[float], q: float) -> float:
    """The q-quantile by nearest rank: the value at index int(q * n), capped at the last."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def faithful(rs: list[Row]) -> int:
    return sum(r["faithful"] for r in rs)


def faithful_lively(rs: list[Row]) -> int:
    return sum(r["faithful"] and r["lively"] >= 2 for r in rs)


def mean(rs: list[Row], field: str) -> str:
    return f"{sum(r[field] for r in rs) / len(rs):.2f}"


def base_variant(variant: str) -> str:
    return variant.replace("+board", "").replace("+roles", "")


def group(rows: Iterable[Row], by: Callable[[Row], str]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = collections.defaultdict(list)
    for row in rows:
        out[by(row)].append(row)
    return out


# Runs 1-3 -----------------------------------------------------------------------------------

V1_FILES = {1: "run1/run1.jsonl", 2: "run2/run2-board.jsonl", 3: "run3/run3-roles.jsonl"}
VARIANTS = ["plain", "name", "traits", "traits+examples", "examples"]


def word_check() -> str:
    """Runs 1-3: the share of comments with no square, move, piece or term outside the facts."""
    rows = []
    for run, path in V1_FILES.items():
        cells = group(read_jsonl(RESULTS / path), lambda r: base_variant(r["variant"]))
        row = [str(run)]
        for variant in VARIANTS:
            rs = cells.get(variant, [])
            ok = sum(
                not violations(
                    r["text"],
                    data.TESTS_V1[r["test"]],
                    render(data.FENS[r["test"]]) if "+board" in r["variant"] else "",
                )
                for r in rs
            )
            row.append(f"{pct(ok, len(rs))} ({len(rs)})" if rs else "-")
        rows.append(row)
    return table(["Run"] + [f"{v} (n)" for v in VARIANTS], rows)


def blind_by_style_v1() -> str:
    """Runs 2-3, traits + examples: the blind judge by style (both runs together)."""
    joined = [r for r in SETS["run2-3"].joined() if base_variant(r["variant"]) == "traits+examples"]
    rows = []
    for style, rs in sorted(
        group(joined, lambda r: r["style"]).items(), key=lambda kv: -faithful(kv[1])
    ):
        rows.append([style, str(len(rs)), pct(faithful(rs), len(rs)), mean(rs, "lively")])
    return table(["Style", "n", "Faithful", "Lively (1-3)"], rows)


def roles_effect() -> str:
    """Run 2 against run 3 (facts labelled by role), traits + examples, all styles."""
    joined = [r for r in SETS["run2-3"].joined() if base_variant(r["variant"]) == "traits+examples"]
    rows = []
    for name, rs in sorted(group(joined, lambda r: r["run"]).items()):
        label = "3 (roles)" if "roles" in name else "2 (no roles)"
        rows.append(
            [label, str(len(rs)), pct(faithful(rs), len(rs)), pct(faithful_lively(rs), len(rs))]
        )
    return table(["Run", "n", "Faithful", "Faithful and lively"], rows)


# Runs 4-8 ------------------------------------------------------------------------------------


def blind_runs_4_8() -> str:
    rows = []
    for name in ("run4", "run5", "run5-12b", "run6", "run7", "run8"):
        for style, rs in group(SETS[name].joined(), lambda r: r["style"]).items():
            rows.append(
                [
                    name.removeprefix("run").replace("-12b", " (12B)"),
                    MODEL_OF_SET[name],
                    style,
                    str(len(rs)),
                    pct(faithful(rs), len(rs)),
                    pct(faithful_lively(rs), len(rs)),
                    mean(rs, "lively"),
                    mean(rs, "clear"),
                ]
            )
    header = ["Run", "Model", "Style", "n", "Faithful", "Faithful and lively", "Lively", "Clear"]
    return table(header, rows)


def coverage(rows: list[Row], tests: dict[str, list[tuple[str, str]]]) -> float:
    """The share of the facts' moves and squares that the comments keep."""
    kept = total = 0
    for r in rows:
        want: set[str] = set()
        for _, fact in tests[r["test"]]:
            squares, moves = tokens(fact)
            want |= squares | moves
        squares, moves = tokens(r["text"])
        kept += len(want & (squares | moves))
        total += len(want)
    return kept / total


def repeating(rows: list[Row], tests: dict[str, list[tuple[str, str]]]) -> int:
    """Comments in which a sentence reuses a colour word of an earlier sentence."""
    count = 0
    for r in rows:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", r["text"].strip()) if s]
        facts = [fact for _, fact in tests[r["test"]]]
        if len(sentences) != len(facts):
            continue
        used: set[str] = set()
        hit = False
        for sentence, fact in zip(sentences, facts, strict=True):
            words = colour_words(sentence, fact)
            hit = hit or bool(words & used)
            used |= words
        count += hit
    return count


def fallback_share(rows: list[Row]) -> float:
    return sum(r["fallback_sentences"] for r in rows) / sum(r["sentences"] for r in rows)


def code_metrics_7_8() -> str:
    rows = []
    for run, path in ((7, "run7/run7-tal.jsonl"), (8, "run8/run8-tal.jsonl")):
        records = read_jsonl(RESULTS / path)
        seconds = [r["seconds"] for r in records]
        rows.append(
            [
                str(run),
                str(len(records)),
                f"{100 * coverage(records, data.TESTS_RUN5):.1f}%",
                f"{100 * fallback_share(records):.1f}%",
                str(repeating(records, data.TESTS_RUN5)),
                f"{nearest_rank(seconds, 0.5):.2f}",
                f"{nearest_rank(seconds, 0.95):.2f}",
            ]
        )
    header = ["Run", "Comments", "Coverage", "Fallback sentences", "Repeating", "p50 s", "p95 s"]
    return table(header, rows)


# Run 8, model comparison --------------------------------------------------------------------


def gpu_gib() -> dict[str, float]:
    out = {"gemma4-e4b-q3km": GIB_Q3}
    for line in (RESULTS / "run8-models" / "models-vram.txt").read_text().splitlines():
        name, _, vram = line.split()
        key = name.replace("scan64-bench-", "").replace(":latest", "").replace(":", "-")
        out[key] = int(vram.split("=")[1]) / 2**30
    return out


def model_records(model: str) -> list[Row]:
    if model == "gemma4-e4b-q3km":
        return read_jsonl(RESULTS / "run8" / "run8-tal.jsonl")
    return read_jsonl(RESULTS / "run8-models" / f"run8-{model}.jsonl")


def models_table() -> str:
    """#462: every model under the run 8 pipeline; two judges, one half of the comments each.

    The judges read 50 comments per model (samples n < 10). The code metrics use every stored
    comment, which for gemma4-e4b Q3_K_M is 100.
    """
    gib = gpu_gib()
    by_model = group(SETS["run8-models"].joined(), lambda r: r["model"])
    rows = []
    for model in sorted(by_model, key=lambda m: (gib[m], LABELS[m])):
        rs = by_model[model]
        halves = [[r for r in rs if r["id"].startswith(f"m{h}")] for h in (0, 1)]
        records = model_records(model)
        rows.append(
            [
                LABELS[model],
                f"{gib[model]:.2f}",
                with_interval(faithful(rs), len(rs)),
                " / ".join(pct(faithful(h), len(h)) for h in halves),
                pct(faithful_lively(rs), len(rs)),
                mean(rs, "lively"),
                mean(rs, "clear"),
                f"{100 * fallback_share(records):.1f}%",
                f"{nearest_rank([r['seconds'] for r in records], 0.95):.2f}",
            ]
        )
    header = [
        "Model",
        "GPU GiB",
        "Faithful (±95%)",
        "Judge 1 / Judge 2",
        "Faithful and lively",
        "Lively",
        "Clear",
        "Fallback sentences",
        "p95 s",
    ]
    return table(header, rows)


def models_coverage() -> str:
    gib = gpu_gib()
    rows = []
    for model in sorted(LABELS, key=lambda m: (gib[m], LABELS[m])):
        records = model_records(model)
        rows.append(
            [LABELS[model], str(len(records)), f"{100 * coverage(records, data.TESTS_RUN5):.1f}%"]
        )
    return table(["Model", "Comments", "Coverage"], rows)


# Run 9, held-out ----------------------------------------------------------------------------

CELL_MODEL = {
    "scan64-bench-gemma4-e4b-q4ks:latest": "gemma4-e4b Q4_K_S",
    "scan64-bench-praxis-4b:latest": "praxis-4b",
}


def sampling(options: Row) -> str:
    if options == {"temperature": 0}:
        return "T 0 (its model file)"
    return f"T {options['temperature']:g}, top_p {options['top_p']:g}, top_k {options['top_k']}"


def heldout_table() -> str:
    by_cell = group(SETS["run9"].joined(), lambda r: r["cell"])
    rows = []
    for cell in sorted(by_cell):
        rs = by_cell[cell]
        records = read_jsonl(RESULTS / "run9" / f"run9-{cell}.jsonl")
        first = records[0]
        rows.append(
            [
                cell.split("-")[0],
                CELL_MODEL.get(first["model"], first["model"]),
                sampling(first["options"]),
                "yes" if first["own"] else "no",
                with_interval(faithful(rs), len(rs)),
                pct(faithful_lively(rs), len(rs)),
                mean(rs, "lively"),
                f"{100 * fallback_share(records):.1f}%",
                f"{nearest_rank([r['seconds'] for r in records], 0.95):.2f}",
            ]
        )
    header = [
        "Cell",
        "Model",
        "Sampling",
        '"Own words" rule',
        "Faithful (±95%)",
        "Faithful and lively",
        "Lively",
        "Fallback sentences",
        "p95 s",
    ]
    return table(header, rows)


HELDOUT_NAMES = {
    "H1-missed-mate": "Missed mate",
    "H2-skewer": "Skewer",
    "H3-stalemate": "Stalemate",
    "H4-pin": "Pin",
    "H5-allowed-fork": "Allowed fork",
}


def heldout_positions() -> str:
    by_cell = group(SETS["run9"].joined(), lambda r: r["cell"])
    rows = []
    for cell in sorted(by_cell):
        per_test = group(by_cell[cell], lambda r: r["test"])
        rows.append(
            [cell.split("-")[0]]
            + [f"{faithful(per_test[t])}/{len(per_test[t])}" for t in HELDOUT_NAMES]
        )
    return table(["Cell"] + list(HELDOUT_NAMES.values()), rows)


SECTIONS: list[tuple[str, Callable[[], str]]] = [
    ("Runs 1-3: word check (code), share of comments that pass", word_check),
    ("Runs 2-3: blind judge by style, traits + examples", blind_by_style_v1),
    ("Run 2 against run 3: facts labelled by role", roles_effect),
    ("Runs 4-8: blind judge by style", blind_runs_4_8),
    ("Runs 7-8: code metrics", code_metrics_7_8),
    ("Run 8 pipeline, 12 models: blind judges (#462)", models_table),
    ("Run 8 pipeline, 12 models: coverage", models_coverage),
    ("Run 9, held-out positions (#462)", heldout_table),
    ("Run 9, faithful per held-out position", heldout_positions),
]


def summarise() -> str:
    """Every table, as Markdown, from the records and judgements in ``results/``."""
    return "\n\n".join(f"### {title}\n\n{build()}" for title, build in SECTIONS) + "\n"


START, END = "<!-- summarise:start -->", "<!-- summarise:end -->"


def doc_tables(text: str) -> str:
    """The tables between the summarise markers of a document."""
    _, rest = text.split(START, 1)
    return rest.split(END, 1)[0].strip("\n")


def write_doc(text: str) -> str:
    """The document with fresh tables between its summarise markers."""
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    return f"{head}{START}\n\n{summarise()}\n{END}{tail}"
