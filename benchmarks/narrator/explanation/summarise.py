"""Rebuild the result tables of docs/evaluation/explanation-quality.md from the stored records."""

import json
from pathlib import Path
from typing import Any

from benchmarks.narrator.explanation import judge, metric
from benchmarks.narrator.voice import blind
from scan64.explanations.themes import ThemeFacts, TurningPointFacts

RESULTS = Path(__file__).resolve().parent / "results" / "run1"
START, END = "<!-- summarise:start -->", "<!-- summarise:end -->"


def share(hits: int, total: int) -> str:
    return f"{hits}/{total} ({100 * hits / total:.0f}%)" if total else "-"


def records(folder: Path = RESULTS) -> list[dict[str, Any]]:
    return [r for path in sorted(folder.glob("*.jsonl")) for r in blind.read_jsonl(path)]


def arms(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault((row["model"].removesuffix(":latest"), row["style"]), []).append(row)
    return dict(sorted(out.items()))


def code_hits(record: dict[str, Any]) -> dict[str, bool]:
    """What the code metric finds in one record, for the fields it can decide."""
    hits = {}
    texts = [s["text"] for s in record["sentences"]]
    if record["theme"]:
        theme = ThemeFacts.model_validate(record["theme"])
        hits["root_cause_named"] = any(metric.states_theme(theme, t) for t in texts)
    if record["turn"]:
        point = TurningPointFacts.model_validate(record["turn"])
        hits["turning_point_named"] = any(metric.states_turn(point, t) for t in texts)
    return hits


def tables(folder: Path = RESULTS) -> str:
    rows = records(folder)
    lines = [
        "### Code metric, by model and voice",
        "",
        "| Model | Voice | Accepted | Root cause stated | by the model | Turning point stated "
        "| Both stated | Grounding pass | First try | Coverage |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (model, voice), group in arms(rows).items():
        r = metric.rates(group)
        lines.append(
            f"| {model} | {voice} | {share(r['accepted'], r['records'])} "
            f"| {share(r['theme_stated'], r['themed'])} "
            f"| {share(r['theme_by_model'], r['themed'])} "
            f"| {share(r['turn_stated'], r['turned'])} | {share(r['both_stated'], r['both'])} "
            f"| {share(r['grounded'], r['sentences'])} | {share(r['first_try'], r['sentences'])} "
            f"| {r['coverage']:.0%} |"
        )
    key = json.loads((folder / "blind-key.json").read_text(encoding="utf-8"))
    judgements = json.loads((folder / "blind-judgements.json").read_text(encoding="utf-8"))
    joined = judge.join(key, judgements)
    lines += [
        "",
        "### Blind judge, by model and voice",
        "",
        "| Model | Voice | n | Root cause named | Turning point named | Cause, effect, line "
        "| Faithful | Root cause named and faithful | Lively (1-3) | Clear (1-3) | Jargon |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (model, voice), group in arms(joined).items():
        n = len(group)
        themed = [g for g in group if g["asks"]["root_cause_named"]]

        def yes(name: str, group: list[dict[str, Any]] = group) -> str:
            asked = [g for g in group if g["asks"][name]]
            return share(sum(g[name] for g in asked), len(asked))

        # An order is right only when nothing it orders is missing; the judge marked two
        # comments as ordered although it marked their cause as not named.
        ordered = [g for g in group if g["asks"]["order"]]
        good = sum(
            g["order"]
            and g["root_cause_named"] is not False
            and g["turning_point_named"] is not False
            for g in ordered
        )
        lines.append(
            f"| {model} | {voice} | {n} | {yes('root_cause_named')} "
            f"| {yes('turning_point_named')} | {share(good, len(ordered))} "
            f"| {share(sum(g['faithful'] for g in group), n)} "
            f"| {share(sum(g['root_cause_named'] and g['faithful'] for g in themed), len(themed))} "
            f"| {sum(g['lively'] for g in group) / n:.2f} "
            f"| {sum(g['clear'] for g in group) / n:.2f} "
            f"| {share(sum(g['jargon'] for g in group), n)} |"
        )
    by_id = {(r["model"], r["style"], r["test"], r["n"]): code_hits(r) for r in rows}
    lines += [
        "",
        "### Code metric against the judge",
        "",
        "| Field | Both yes | Code yes, judge no | Code no, judge yes | Both no | Agree |",
        "|---|---|---|---|---|---|",
    ]
    for name in ("root_cause_named", "turning_point_named"):
        pairs = [
            (by_id[j["model"], j["style"], j["test"], j["n"]][name], j[name])
            for j in joined
            if j["asks"][name]
        ]
        counts = [pairs.count(p) for p in ((True, True), (True, False), (False, True))]
        none = pairs.count((False, False))
        lines.append(
            f"| {name} | {counts[0]} | {counts[1]} | {counts[2]} | {none} "
            f"| {share(counts[0] + none, len(pairs))} |"
        )
    return "\n".join(lines) + "\n"


def write_doc(page: str, folder: Path = RESULTS) -> str:
    head, rest = page.split(START)
    return f"{head}{START}\n\n{tables(folder)}\n{END}{rest.split(END)[1]}"
