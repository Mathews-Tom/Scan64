"""Blind judging sets: shuffled items with no labels, a key that unblinds them, and the join back.

The judge sees only ``position``, ``facts`` and ``comment``. The key, the run records and the
code stay out of the judge's view. ``SETS`` holds the exact recipe of every stored blind set, so
``build`` reproduces each one from the stored records.
"""

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.narrator.voice import data
from benchmarks.narrator.voice.render import render

RESULTS = Path(__file__).resolve().parent / "results"
Row = dict[str, Any]


def facts_plain(test: str) -> list[str]:
    return data.TESTS_V1[test]


def _labelled(tests: dict[str, list[tuple[str, str]]]) -> Callable[[str], list[str]]:
    return lambda test: [f"{role}: {text}" for role, text in tests[test]]


FACTS: dict[str, Callable[[str], list[str]]] = {
    "v1": facts_plain,
    "run4": _labelled(data.TESTS_RUN4),
    "run5": _labelled(data.TESTS_RUN5),
    "heldout": _labelled(data.TESTS_HELDOUT),
}


def fen_of(test: str) -> str:
    return data.FENS_HELDOUT[test] if test in data.FENS_HELDOUT else data.FENS[test]


def build(
    rows: list[Row],
    *,
    seed: int,
    prefix: str,
    facts: str,
    key_fields: tuple[str, ...],
    halves_by: str | None = None,
) -> tuple[list[list[Row]], dict[str, Row]]:
    """Shuffle rows into blind items; return the item lists (one, or two halves) and the key.

    With ``halves_by``, each arm (the rows sharing that field) is shuffled and split in two, so
    both halves hold the same number of comments from every arm; then each half is shuffled.
    """
    rng = random.Random(seed)
    if halves_by is None:
        rows = list(rows)
        rng.shuffle(rows)
        groups = [rows]
    else:
        arms: dict[str, list[Row]] = {}
        for row in rows:
            arms.setdefault(row[halves_by], []).append(row)
        groups = [[], []]
        for arm in arms.values():
            rng.shuffle(arm)
            groups[0] += arm[: len(arm) // 2]
            groups[1] += arm[len(arm) // 2 :]
    out: list[list[Row]] = []
    key: dict[str, Row] = {}
    for h, group in enumerate(groups):
        if halves_by is not None:
            rng.shuffle(group)
        items = []
        for i, row in enumerate(group, 1):
            item_id = f"{prefix}{h if halves_by else ''}{i:03d}"
            items.append(
                {
                    "id": item_id,
                    "position": render(fen_of(row["test"])),
                    "facts": FACTS[facts](row["test"]),
                    "comment": row["text"],
                }
            )
            key[item_id] = {k: row[k] for k in key_fields}
        out.append(items)
    return out, key


def join(key: dict[str, Row], judgements: list[Row]) -> list[Row]:
    """Each judged item with its key; raises when an item of the key has no judgement."""
    judged = {j["id"]: j for j in judgements}
    missing = sorted(set(key) - set(judged))
    if missing:
        raise ValueError(f"{len(missing)} items have no judgement, e.g. {missing[:3]}")
    return [key[i] | judged[i] | {"id": i} for i in key]


def read_jsonl(path: Path) -> list[Row]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@dataclass(frozen=True)
class BlindSet:
    """How one stored blind set was built, and where its files are."""

    folder: str
    name: str
    records: tuple[str, ...]
    seed: int
    prefix: str
    facts: str
    key_fields: tuple[str, ...]
    # The arm label in the key: "file" or "stem" (the record file's name), "model", "cell",
    # or any other value, stored as the run name itself.
    label: str = "stem"
    first_n: int | None = None  # keep samples n < first_n (or n == 0 when first_n == 1)
    halves_by: str | None = None

    def rows(self) -> list[Row]:
        rows = []
        for record in self.records:
            path = RESULTS / record
            for row in read_jsonl(path):
                if self.first_n is not None and row["n"] >= self.first_n:
                    continue
                rows.append(row | self.labels(path.name))
        return rows

    def labels(self, file_name: str) -> Row:
        stem = file_name.removesuffix(".jsonl")
        if self.label == "file":
            return {"run": file_name}
        if self.label == "stem":
            return {"run": stem}
        if self.label == "model":
            return {"model": stem.removeprefix("run8-").replace("tal", "gemma4-e4b-q3km")}
        if self.label == "cell":
            return {"cell": stem.removeprefix("run9-")}
        return {"run": self.label}

    def item_paths(self) -> list[Path]:
        if self.halves_by:
            return [RESULTS / self.folder / f"{self.name}{h}-items.json" for h in (0, 1)]
        return [RESULTS / self.folder / f"{self.name}-items.json"]

    def key_path(self) -> Path:
        return RESULTS / self.folder / f"{self.name}-key.json"

    def judgement_paths(self) -> list[Path]:
        return [Path(str(p).replace("-items.json", "-judgements.json")) for p in self.item_paths()]

    def build(self) -> tuple[list[list[Row]], dict[str, Row]]:
        return build(
            self.rows(),
            seed=self.seed,
            prefix=self.prefix,
            facts=self.facts,
            key_fields=self.key_fields,
            halves_by=self.halves_by,
        )

    def joined(self) -> list[Row]:
        key = json.loads(self.key_path().read_text(encoding="utf-8"))
        judgements = [j for p in self.judgement_paths() for j in json.loads(p.read_text())]
        return join(key, judgements)


RUN8_MODELS = (
    "caissa-m1-8b",
    "gemma3-1b",
    "gemma3-270m",
    "gemma4-e4b-q4ks",
    "llama3.2-1b",
    "praxis-2b",
    "praxis-4b",
    "qwen3-1.7b",
    "qwen3.5-2b",
    "qwen3.5-4b",
    "second-2b",
)
RUN9_CELLS = ("A-gemma", "B-gemma-own", "C-praxis-t0", "D-praxis-t1", "E-praxis-t1-own")
RUN_KEY = ("run", "test", "variant", "style")

SETS = {
    "run2-3": BlindSet(
        "run2-3-blind",
        "blind",
        ("run2/run2-board.jsonl", "run3/run3-roles.jsonl"),
        454,
        "c",
        "v1",
        RUN_KEY,
        label="file",
        first_n=1,
    ),
    "run4": BlindSet("run4", "blind4", ("run4/run4.jsonl",), 4544, "d", "run4", RUN_KEY),
    "run5": BlindSet("run5", "blind5", ("run5/run5.jsonl",), 4545, "e", "run5", RUN_KEY),
    "run5-12b": BlindSet("run5", "blind5b", ("run5/run5-12b.jsonl",), 4546, "f", "run5", RUN_KEY),
    "run6": BlindSet("run6", "blind6", ("run6/run6.jsonl",), 4547, "g", "run5", RUN_KEY),
    "run7": BlindSet(
        "run7", "blind7", ("run7/run7-tal.jsonl",), 4548, "h", "run5", RUN_KEY, "run7"
    ),
    "run8": BlindSet(
        "run8", "blind8", ("run8/run8-tal.jsonl",), 4549, "k", "run5", RUN_KEY, "run8"
    ),
    "run8-models": BlindSet(
        "run8-models",
        "blindm",
        tuple(f"run8-models/run8-{m}.jsonl" for m in RUN8_MODELS) + ("run8/run8-tal.jsonl",),
        4550,
        "m",
        "run5",
        ("model", "test"),
        label="model",
        first_n=10,
        halves_by="model",
    ),
    "run9": BlindSet(
        "run9",
        "blind9",
        tuple(f"run9/run9-{c}.jsonl" for c in RUN9_CELLS),
        4620,
        "h",
        "heldout",
        ("cell", "test"),
        label="cell",
    ),
}
