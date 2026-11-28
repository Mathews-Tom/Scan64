"""Rebuild the narrator benchmark positions with a live engine (issue #389).

Usage:
    taskset -c 32-39 uv run python -m benchmarks.narrator.build_positions_live \\
        benchmarks/narrator/narrator.toml
    uv run python -m benchmarks.narrator.build_positions_live \\
        benchmarks/narrator/narrator.toml --report-only

``positions.json`` comes from recorded Stockfish searches. This script takes the
same games and the same learner moves and runs Scan64's own pipeline on them with
the engine profile in ``[positions.live].engine_config`` (selected through
``SCAN64_ENGINE_CONFIG``): ``FastPassOrchestrator`` over the whole game, the
focused search on the position after the move, the search before the move that
serves as mate source, ``compose_candidate_evidence``, every registered
detector and ``arbitrate_diagnoses``. Positions keep their ``position_id``.

A position the engine does not flag (swing below the fast-pass threshold) or
does not diagnose is not written to ``positions``; it is listed in
``comparison`` with its status, so the shortfall is in the file.

Determinism: every search is a fresh engine process (``StockfishAdapter``) with a
fixed node budget, the profile's thread count and hash size. ``nps`` is wall-clock
dependent and is dropped from the stored lines and evidence. Analysis UUIDs are
replaced by UUID5 values derived from the position id, so a rerun writes the same
bytes when the engine is deterministic.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import tomllib
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from chess_lesson_spec import Diagnosis

from benchmarks.narrator.build_positions import REPO_ROOT, _detectors, diagnose_candidate
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import (
    CandidatePosition,
    FastPassConfig,
    FastPassOrchestrator,
)
from scan64.explanations.assembly import grounding_context_for
from scan64.learning.evaluation.real_games import RecordedAdapter
from scan64.learning.evidence.composer import needs_mate_source
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.providers.engine.config import ENGINE_CONFIG_ENV, load_engine_profile
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig

UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Same fast-pass threshold as the analysis job and build_positions.py.
SWING_THRESHOLD_CP = 150
SLOPE_WINDOW_CP = 400


class _BoundedAdapter:
    """Caps concurrent engine processes across every game and pass."""

    def __init__(self, inner: StockfishAdapter, limit: int) -> None:
        self._inner = inner
        self._gate = asyncio.Semaphore(limit)

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        async with self._gate:
            return await self._inner.analyze_position(
                fen, nodes=nodes, depth=depth, time_ms=time_ms, multipv=multipv
            )


def _line(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """First MultiPV line without wall-clock fields."""
    first = raw[0]
    return {
        key: first[key]
        for key in ("pv", "score_cp", "score_mate", "wdl", "depth", "seldepth", "nodes")
        if key in first
    }


def _without_nps(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_nps(item) for key, item in value.items() if key != "nps"}
    if isinstance(value, list):
        return [_without_nps(item) for item in value]
    return value


def _stable_uuids(record: dict[str, Any], position_id: str) -> dict[str, Any]:
    """Drop wall-clock ``nps`` and replace random analysis UUIDs with UUID5 values."""
    text = json.dumps(_without_nps(record), sort_keys=True)
    mapping: dict[str, str] = {}
    for found in UUID_PATTERN.findall(text):
        if found not in mapping:
            mapping[found] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{position_id}#{len(mapping)}"))
    result: dict[str, Any] = json.loads(UUID_PATTERN.sub(lambda m: mapping[m.group(0)], text))
    return result


def _facts_error(record: dict[str, Any]) -> str | None:
    try:
        grounding_context_for(
            Diagnosis(**record["diagnosis"]),
            tuple(Evidence(**item) for item in record["evidence"]),
            record["fen"],
        )
    except Exception as error:  # every failure is reported, not raised
        return f"{type(error).__name__}: {error}"
    return None


async def _game_positions(
    adapter: _BoundedAdapter,
    budgets: dict[str, int],
    corpus_path: Path,
    game: dict[str, Any],
    references: list[dict[str, Any]],
    detectors: tuple[PatternDetector, ...],
) -> list[tuple[dict[str, Any] | None, dict[str, Any]]]:
    fens = RecordedAdapter(game).fens
    fast_pass = await FastPassOrchestrator(
        adapter,
        FastPassConfig(
            nodes=budgets["fast_nodes"],
            swing_threshold_cp=SWING_THRESHOLD_CP,
            concurrency=budgets["concurrency"],
        ),
    ).run_fast_pass(game["moves"])
    flagged = {candidate.move_index: candidate for candidate in fast_pass.candidates}

    async def one(reference: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        index = reference["move_index"]
        stockfish_payload = reference["evidence"][0]["payload"]
        row: dict[str, Any] = {
            "position_id": reference["position_id"],
            "stockfish": {
                "primary": reference["diagnosis"]["primary"],
                "swing_cp": stockfish_payload.get("swing_cp"),
                "fast_before": _line(game["fast"][index]),
                "fast_after": _line(game["fast"][index + 1]),
                "deep_before": _line(game["deep_before"][str(index)]),
                "focused_after": _line(game["focused"][str(index)]),
            },
        }
        before, after = fast_pass.evaluations[index], fast_pass.evaluations[index + 1]
        deep_task = adapter.analyze_position(
            fens[index], nodes=budgets["deep_nodes"], multipv=budgets["deep_multipv"]
        )
        focused_task = adapter.analyze_position(
            fens[index + 1], nodes=budgets["focused_nodes"], multipv=budgets["focused_multipv"]
        )
        deep, focused = await asyncio.gather(deep_task, focused_task)
        candidate = flagged.get(index)
        caissa: dict[str, Any] = {
            "swing_cp": candidate.swing_cp if candidate is not None else None,
            "fast_before": _line(before.raw_result),
            "fast_after": _line(after.raw_result),
            "deep_before": _line(deep.raw_result),
            "focused_after": _line(focused.raw_result),
            "primary": None,
            "secondary": [],
        }
        row["engine"] = caissa
        if candidate is None:
            row["status"] = "not_flagged"
            return None, row
        record = await diagnose_candidate(
            corpus_path=corpus_path,
            game=game,
            fens=fens,
            candidate=CandidatePosition(
                fen=candidate.fen,
                move_index=index,
                before_analysis=before,
                after_analysis=after,
                swing_cp=candidate.swing_cp,
            ),
            focused_analysis=focused,
            mate_source_analysis=deep if needs_mate_source(after) else None,
            detectors=detectors,
        )
        if record is None:
            row["status"] = "no_diagnosis"
            return None, row
        record = _stable_uuids(record, reference["position_id"])
        caissa["primary"] = record["diagnosis"]["primary"]
        caissa["secondary"] = record["diagnosis"]["secondary"]
        error = _facts_error(record)
        row["facts_error"] = error
        row["status"] = "diagnosed" if error is None else "facts_error"
        return (record if error is None else None), row

    return list(await asyncio.gather(*(one(reference) for reference in references)))


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def build(config_path: Path) -> Path:
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)
    spec = raw["positions"]["live"]
    profile_path = (REPO_ROOT / spec["engine_config"]).resolve()
    os.environ[ENGINE_CONFIG_ENV] = str(profile_path)
    profile = load_engine_profile(profile_path)
    engine_dir = Path(profile.binary_path).parent
    binary = Path(spec["engine_binary"]).expanduser()
    budgets = {
        key: int(spec[key])
        for key in (
            "fast_nodes",
            "focused_nodes",
            "focused_multipv",
            "deep_nodes",
            "deep_multipv",
            "concurrency",
        )
    }
    inner = StockfishAdapter(StockfishConfig())
    adapter = _BoundedAdapter(inner, budgets["concurrency"])
    identity = await inner.analysis_identity()
    reference_path = REPO_ROOT / raw["positions"]["file"]
    references = json.loads(reference_path.read_text(encoding="utf-8"))["positions"]
    by_game: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for reference in references:
        by_game.setdefault((reference["corpus"], reference["game_id"]), []).append(reference)
    corpora: dict[str, dict[str, Any]] = {}
    detectors = _detectors()
    jobs = []
    for (corpus, game_id), items in by_game.items():
        if corpus not in corpora:
            corpora[corpus] = json.loads((REPO_ROOT / corpus).read_text(encoding="utf-8"))
        game = next(g for g in corpora[corpus]["games"] if g["id"] == game_id)
        jobs.append(_game_positions(adapter, budgets, REPO_ROOT / corpus, game, items, detectors))
    results = {
        row["position_id"]: (record, row)
        for game_results in await asyncio.gather(*jobs)
        for record, row in game_results
    }
    ordered = [results[reference["position_id"]] for reference in references]
    positions = [record for record, _ in ordered if record is not None]
    output: Path = REPO_ROOT / spec["file"]
    output.write_text(
        json.dumps(
            {
                "source": (
                    "same games and learner moves as "
                    f"{raw['positions']['file']}, live engine, Scan64 diagnosis pipeline"
                ),
                "reference_positions": raw["positions"]["file"],
                "reference_sha256": _sha256(reference_path),
                "engine": {
                    "profile": spec["engine_config"],
                    "name": profile.name,
                    "id_name": identity.get("engine_name"),
                    "binary": spec["engine_binary"],
                    "binary_sha256": _sha256(binary),
                    "network": {
                        path.name: _sha256(path) for path in sorted(engine_dir.glob("*.pnn"))
                    },
                    "threads": profile.threads,
                    "hash_mb": profile.hash_size,
                    "options": dict(profile.options),
                },
                "budgets": {**budgets, "swing_threshold_cp": SWING_THRESHOLD_CP},
                "corpora": raw["positions"]["corpora"],
                "per_code": dict(
                    sorted(Counter(p["diagnosis"]["primary"] for p in positions).items())
                ),
                "status": dict(sorted(Counter(row["status"] for _, row in ordered).items())),
                "positions": positions,
                "comparison": [row for _, row in ordered],
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def _score(line: dict[str, Any]) -> int | None:
    value = line.get("score_cp")
    return int(value) if value is not None else None


def _shown(line: dict[str, Any]) -> str:
    if "score_mate" in line:
        return f"mate {line['score_mate']}"
    return f"{line.get('score_cp')} cp"


def _slope(pairs: list[tuple[int, int]]) -> tuple[float, float, float, int]:
    """OLS and reduced-major-axis slopes of engine cp on Stockfish cp, Pearson r, n."""
    window = [(s, e) for s, e in pairs if abs(s) <= SLOPE_WINDOW_CP]
    if len(window) < 3:
        return float("nan"), float("nan"), float("nan"), len(window)
    xs, ys = [s for s, _ in window], [e for _, e in window]
    r = statistics.correlation(xs, ys)
    axis = statistics.stdev(ys) / statistics.stdev(xs) * (1 if r >= 0 else -1)
    return statistics.linear_regression(xs, ys).slope, axis, r, len(window)


def report(config_path: Path) -> Path:
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)
    spec = raw["positions"]["live"]
    data = json.loads((REPO_ROOT / spec["file"]).read_text(encoding="utf-8"))
    rows = data["comparison"]
    total = len(rows)
    engine = data["engine"]
    diagnosed = [row for row in rows if row["engine"]["primary"] is not None]
    same_code = [
        row for row in diagnosed if row["engine"]["primary"] == row["stockfish"]["primary"]
    ]
    status = Counter(row["status"] for row in rows)

    def agree(key: str) -> int:
        return sum(row["engine"][key]["pv"][0] == row["stockfish"][key]["pv"][0] for row in rows)

    lines = [
        "# Caissa evidence on the 50 narrator benchmark positions (#389)",
        "",
        f"Engine: {engine['id_name']} (`{engine['name']}`), binary sha256 "
        f"`{engine['binary_sha256']}`, network "
        + ", ".join(f"`{name}` sha256 `{digest}`" for name, digest in engine["network"].items())
        + f". Threads {engine['threads']}, Hash {engine['hash_mb']} MB.",
        f"Budgets: fast pass {data['budgets']['fast_nodes']:,} nodes; focused (after the move) "
        f"{data['budgets']['focused_nodes']:,} nodes MultiPV {data['budgets']['focused_multipv']}; "
        f"before the move (mate source) {data['budgets']['deep_nodes']:,} nodes MultiPV "
        f"{data['budgets']['deep_multipv']}. Reference: `{data['reference_positions']}` "
        "(recorded Stockfish searches, same budgets).",
        "Output: `benchmarks/narrator/positions-caissa.json`, built by "
        "`benchmarks/narrator/build_positions_live.py`.",
        "",
        "## Diagnosis",
        "",
        "| Outcome | Positions |",
        "| --- | --- |",
        f"| Flagged by the fast pass (swing > {SWING_THRESHOLD_CP} cp) | "
        f"{total - status['not_flagged']} of {total} |",
        f"| Diagnosed as a mistake (trusted code) | {len(diagnosed)} of {total} |",
        f"| Same primary code as Stockfish | {len(same_code)} of {total} |",
        f"| NarratorFacts builds | {status['diagnosed']} of {len(diagnosed)} diagnosed |",
        f"| Written to positions-caissa.json | {len(data['positions'])} of {total} |",
        "",
        "Primary code per position class (Stockfish code: Caissa result):",
        "",
        "| Stockfish code | Positions | Caissa same code | Other code | No diagnosis "
        "| Not flagged |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for code in sorted({row["stockfish"]["primary"] for row in rows}):
        group = [row for row in rows if row["stockfish"]["primary"] == code]
        same = sum(row["engine"]["primary"] == code for row in group)
        other = sum(row["engine"]["primary"] not in (None, code) for row in group)
        lines.append(
            f"| `{code}` | {len(group)} | {same} | {other} | "
            f"{sum(row['status'] == 'no_diagnosis' for row in group)} | "
            f"{sum(row['status'] == 'not_flagged' for row in group)} |"
        )
    changed = [row for row in diagnosed if row not in same_code]
    if changed:
        lines += ["", "Positions with a different primary code:", ""]
        lines += [
            f"- `{row['position_id']}`: Stockfish `{row['stockfish']['primary']}`, "
            f"Caissa `{row['engine']['primary']}`"
            for row in changed
        ]
    missing = [row for row in rows if row["engine"]["primary"] is None]
    if missing:
        lines += ["", "Positions without a Caissa diagnosis (shortfall):", ""]
        lines += [
            f"- `{row['position_id']}` ({row['stockfish']['primary']}): {row['status']}, "
            f"Caissa swing {row['engine']['swing_cp']} cp, Stockfish swing "
            f"{row['stockfish']['swing_cp']} cp; Caissa fast pass before/after "
            f"{_shown(row['engine']['fast_before'])}/{_shown(row['engine']['fast_after'])}"
            for row in missing
        ]
    lines += [
        "",
        "## Best move",
        "",
        "| Search | Same first move as Stockfish |",
        "| --- | --- |",
        f"| Fast pass before the move ({data['budgets']['fast_nodes']:,} nodes) | "
        f"{agree('fast_before')} of {total} |",
        f"| Before the move ({data['budgets']['deep_nodes']:,} nodes, MultiPV "
        f"{data['budgets']['deep_multipv']}) | {agree('deep_before')} of {total} |",
        f"| After the move, the refutation ({data['budgets']['focused_nodes']:,} nodes, "
        f"MultiPV {data['budgets']['focused_multipv']}) | {agree('focused_after')} of {total} |",
        "",
        "## Evaluation",
        "",
        "Scores from the side to move, centipawns. Mate scores are excluded. Slopes use "
        f"the pairs where Stockfish |cp| <= {SLOPE_WINDOW_CP}: OLS is least squares of "
        "Caissa cp on Stockfish cp; RMA (reduced major axis, sd ratio) treats both "
        "engines as noisy.",
        "",
        "| Search | Pairs | Median abs diff (cp) | Share above 50 cp | Slope OLS | Slope RMA "
        "| r | Slope n |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    all_pairs: list[tuple[int, int]] = []
    for key, label in (
        ("fast_before", "Fast pass before"),
        ("fast_after", "Fast pass after"),
        ("deep_before", "Before, 1M nodes"),
        ("focused_after", "After, 1M nodes"),
    ):
        pairs = [
            (s, e)
            for row in rows
            if (s := _score(row["stockfish"][key])) is not None
            and (e := _score(row["engine"][key])) is not None
        ]
        all_pairs += pairs if key in ("deep_before", "focused_after") else []
        diffs = [abs(e - s) for s, e in pairs]
        slope, axis, r, n = _slope(pairs)
        lines.append(
            f"| {label} | {len(pairs)} | {statistics.median(diffs):.0f} | "
            f"{sum(d > 50 for d in diffs) / len(diffs):.0%} | {slope:.2f} | {axis:.2f} | "
            f"{r:.3f} | {n} |"
        )
    slope, axis, r, n = _slope(all_pairs)
    diffs = [abs(e - s) for s, e in all_pairs]
    large = [(abs(s), abs(e)) for s, e in all_pairs if abs(s) > 700]
    lines += [
        f"| 1M-node searches pooled | {len(all_pairs)} | {statistics.median(diffs):.0f} | "
        f"{sum(d > 50 for d in diffs) / len(diffs):.0%} | {slope:.2f} | {axis:.2f} | "
        f"{r:.3f} | {n} |",
        "",
        f"Large scores (1M-node searches, Stockfish |cp| > 700, n = {len(large)}): "
        f"Stockfish median |cp| {statistics.median(s for s, _ in large):.0f}, Caissa median "
        f"|cp| {statistics.median(e for _, e in large):.0f}. Caissa reports winning "
        "positions near 600-700 cp where Stockfish reports 1,000 cp and more, so a move that "
        "keeps a won position won shows a smaller swing. Caissa also reports some won "
        "positions as cp scores above 20,000 instead of a mate score.",
        "",
        "The #396 comparison found slope 1.19 (n = 31, 1 s per search, the position before "
        "the move only). This sample uses fixed node budgets and both sides of each "
        "mistake; it gives a slope below 1. One score-scale factor does not fit both "
        "samples, so a per-engine scale needs a larger fit.",
        "",
        "## NarratorFacts",
        "",
    ]
    errors = [row for row in rows if row.get("facts_error")]
    if errors:
        lines += [f"- `{row['position_id']}`: `{row['facts_error']}`" for row in errors]
    else:
        lines.append(
            f"Every diagnosed position ({len(diagnosed)}) builds NarratorFacts "
            "(`grounding_context_for`) without an error."
        )
    lines += [
        "",
        "## Reproduce",
        "",
        "```",
        "taskset -c 32-39 uv run python -m benchmarks.narrator.build_positions_live "
        "benchmarks/narrator/narrator.toml",
        "```",
        "",
    ]
    output: Path = REPO_ROOT / spec["report"]
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--report-only", action="store_true", help="rewrite the report only")
    args = parser.parse_args()
    if not args.report_only:
        print(f"wrote {asyncio.run(build(args.config))}")
    print(f"wrote {report(args.config)}")


if __name__ == "__main__":
    main()
