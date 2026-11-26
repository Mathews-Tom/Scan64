"""Benchmark open LLMs as the Scan64 narrator (issue #369).

Usage (repository root, Ollama serving at each model's ``base_url``):
    uv run python -m benchmarks.narrator.run_benchmark benchmarks/narrator/narrator.toml --setup
    uv run python -m benchmarks.narrator.run_benchmark CONFIG --models praxis-4b qwen3.5-4b
    uv run python -m benchmarks.narrator.run_benchmark CONFIG --resume RUNS.jsonl
    uv run python -m benchmarks.narrator.run_benchmark CONFIG --summarize RUNS.jsonl

Every generation goes through the production path: the configured
``pipeline_entry`` (default ``resolve_explanation``) with an
``OllamaExplanationAdapter`` built by ``create_llm_provider`` as the local
provider. The adapter is wrapped only to time the call and keep its output.
The pipeline's own validator decides acceptance; the accepted/fallback outcome
and the fallback reason come from the pipeline (``template_fallback_counts``).
Each claim is then validated on its own against the same grounding context to
count rejected claims per explanation and their reasons.

One JSON line per (model, position, run) is appended to ``runs_<stamp>.jsonl``
as soon as it finishes; ``--resume`` skips lines already present. The summary
is written to ``summary_<stamp>.json`` and ``summary_<stamp>.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import inspect
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import chess
import httpx
from chess_lesson_spec import Diagnosis, Explanation

from scan64.explanations import assembly
from scan64.explanations.validator import (
    _SAN_TOKEN,
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    _ClaimEvidence,
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import (
    ExplanationRequest,
    GeneratedExplanation,
    LLMProviderConfig,
    create_llm_provider,
)
from scan64.providers.llm.adapters import LLMExplanationProvider

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELFILES = Path(__file__).resolve().parent / "modelfiles"
MODEL_PREFIX = "scan64-bench-"
LOG = logging.getLogger("narrator_benchmark")

# Reasons that state a wrong chess fact (prose or cited line). The others are
# contract violations (unknown evidence id, certainty or disclosure too high).
CHESS_FACT_REASONS = frozenset(
    {
        "malformed_line_move",
        "illegal_line_move",
        "unverified_line",
        "illegal_move",
        "move_not_verified",
        "piece_not_on_square",
        "piece_not_present",
        "square_not_in_evidence",
        "evaluation_not_supported",
        "mate_not_supported",
        "check_not_supported",
        "material_not_supported",
        "move_type_not_supported",
        "tactic_not_supported",
        "wrong_side_to_move",
        "unparsed_chess_token",
    }
)

PipelineEntry = Callable[..., Awaitable[Explanation]]
ContextBuilder = Callable[[Diagnosis, Sequence[Evidence], str], GroundedExplanationContext]


@dataclass(frozen=True)
class ModelEntry:
    name: str
    role: str
    licence: str
    params_b: float
    base_model: str
    training_data: str
    ollama_tag: str | None
    hf_repo: str | None
    hf_revision: str | None
    hf_file: str | None
    sha256: str | None
    base_url: str

    @property
    def served_name(self) -> str:
        return f"{MODEL_PREFIX}{self.name}"


@dataclass(frozen=True)
class BenchConfig:
    runs_per_position: int
    results_dir: Path
    gguf_cache: Path
    request_timeout_s: float
    production_time_limit_s: float
    num_ctx: int
    num_predict: int
    request_overrides: dict[str, Any]
    foreign_ollama: tuple[str, ...]
    pipeline_entry: str
    grounding_context: str
    positions_file: Path
    models: tuple[ModelEntry, ...]


def load_config(path: Path) -> BenchConfig:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    run = raw["run"]
    default_url = run["base_url"]
    models = tuple(
        ModelEntry(
            name=entry["name"],
            role=entry["role"],
            licence=entry["licence"],
            params_b=float(entry["params_b"]),
            base_model=entry["base_model"],
            training_data=entry["training_data"],
            ollama_tag=entry.get("ollama_tag"),
            hf_repo=entry.get("hf_repo"),
            hf_revision=entry.get("hf_revision"),
            hf_file=entry.get("hf_file"),
            sha256=entry.get("sha256"),
            base_url=entry.get("base_url", default_url),
        )
        for entry in raw["models"]
    )
    for model in models:
        if (model.ollama_tag is None) == (model.hf_file is None):
            raise ValueError(f"Model {model.name}: set exactly one of ollama_tag or hf_file")
        if not (MODELFILES / f"{model.name}.Modelfile").is_file():
            raise ValueError(f"Model {model.name}: missing modelfiles/{model.name}.Modelfile")
    return BenchConfig(
        runs_per_position=int(run["runs_per_position"]),
        results_dir=REPO_ROOT / run["results_dir"],
        gguf_cache=Path(run["gguf_cache"]).expanduser(),
        request_timeout_s=float(run["request_timeout_s"]),
        production_time_limit_s=float(run["production_time_limit_s"]),
        num_ctx=int(run["num_ctx"]),
        num_predict=int(run["num_predict"]),
        request_overrides=dict(run["request_overrides"]),
        foreign_ollama=tuple(run["foreign_ollama"]),
        pipeline_entry=run["pipeline_entry"],
        grounding_context=run["grounding_context"],
        positions_file=REPO_ROOT / raw["positions"]["file"],
        models=models,
    )


def _resolve(dotted: str) -> Any:
    module_name, _, attribute = dotted.partition(":")
    if not attribute:
        raise ValueError(f"Expected 'module:attribute', got {dotted!r}")
    return getattr(importlib.import_module(module_name), attribute)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def pipeline_identity(config: BenchConfig) -> dict[str, Any]:
    """Pin the prompt builder under test: commit, dirty flag, module source hash."""
    entry = _resolve(config.pipeline_entry)
    source_file = Path(inspect.getfile(entry))
    return {
        "pipeline_entry": config.pipeline_entry,
        "grounding_context": config.grounding_context,
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain", "--", "src")),
        "module_file": source_file.relative_to(REPO_ROOT).as_posix(),
        "module_sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
    }


# --- Setup ---------------------------------------------------------------------


def _ollama(model: ModelEntry, path: str, body: dict[str, Any], timeout: float) -> Any:
    response = httpx.post(f"{model.base_url}{path}", json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_gguf(model: ModelEntry, cache: Path) -> Path:
    assert model.hf_repo and model.hf_revision and model.hf_file and model.sha256
    target = cache / model.name / model.hf_file
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{model.hf_repo}/resolve/{model.hf_revision}/{model.hf_file}"
        LOG.info("downloading %s", url)
        partial = target.with_suffix(target.suffix + ".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=600) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(1 << 20):
                    stream.write(chunk)
        partial.rename(target)
    actual = _sha256(target)
    if actual != model.sha256:
        raise RuntimeError(f"{target}: sha256 {actual} != pinned {model.sha256}")
    return target


def setup_models(config: BenchConfig, ollama_binary: str) -> None:
    """Create one Ollama model per entry from its Modelfile (idempotent)."""
    build_dir = config.gguf_cache / "modelfiles"
    build_dir.mkdir(parents=True, exist_ok=True)
    for model in config.models:
        text = (MODELFILES / f"{model.name}.Modelfile").read_text(encoding="utf-8")
        text = text.replace("@NUM_CTX@", str(config.num_ctx))
        text = text.replace("@NUM_PREDICT@", str(config.num_predict))
        if model.ollama_tag is not None:
            LOG.info("pulling %s", model.ollama_tag)
            _ollama(model, "/api/pull", {"model": model.ollama_tag, "stream": False}, 3600)
        else:
            text = text.replace("@GGUF@", _download_gguf(model, config.gguf_cache).as_posix())
        rendered = build_dir / f"{model.name}.Modelfile"
        rendered.write_text(text, encoding="utf-8")
        host = model.base_url.removeprefix("http://")
        LOG.info("creating %s", model.served_name)
        subprocess.run(
            [ollama_binary, "create", model.served_name, "-f", str(rendered)],
            check=True,
            env={**os.environ, "OLLAMA_HOST": host},
        )


# --- Measurement ---------------------------------------------------------------


@dataclass
class Position:
    position_id: str
    fen: str
    code: str
    diagnosis: Diagnosis
    evidence: tuple[Evidence, ...]


def load_positions(path: Path) -> list[Position]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Position(
            position_id=item["position_id"],
            fen=item["fen"],
            code=item["diagnosis"]["primary"],
            diagnosis=Diagnosis(**item["diagnosis"]),
            evidence=tuple(Evidence(**evidence) for evidence in item["evidence"]),
        )
        for item in raw["positions"]
    ]


class _MeteredTransport(httpx.AsyncBaseTransport):
    """Merges ``request_overrides`` into each chat request and keeps the token
    counts Ollama reports (the adapter discards them)."""

    def __init__(self, overrides: dict[str, Any]) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self._overrides = overrides
        self.last_body: dict[str, Any] | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._overrides and request.url.path.endswith("/api/chat"):
            body = {**json.loads(await request.aread()), **self._overrides}
            request = httpx.Request(
                request.method,
                request.url,
                headers={
                    key: value
                    for key, value in request.headers.items()
                    if key.lower() != "content-length"
                },
                json=body,
            )
        response = await self._inner.handle_async_request(request)
        content = await response.aread()
        try:
            reply: object = json.loads(content)
        except json.JSONDecodeError:
            reply = None
        self.last_body = cast(dict[str, Any], reply) if isinstance(reply, dict) else None
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=content,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


class RecordingProvider:
    """Times the wrapped provider and keeps its request and raw output."""

    def __init__(self, inner: LLMExplanationProvider) -> None:
        self._inner = inner
        self.request: ExplanationRequest | None = None
        self.generated: GeneratedExplanation | None = None
        self.error: str | None = None
        self.latency_s: float | None = None

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        self.request = request
        started = time.perf_counter()
        try:
            self.generated = await self._inner.generate(request)
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}; cause={error.__cause__!r}"
            raise
        finally:
            self.latency_s = time.perf_counter() - started
        return self.generated


def _claim_reason(claim: Any, context: GroundedExplanationContext) -> tuple[str | None, str | None]:
    try:
        validate_generated_explanation(GeneratedExplanation(claims=(claim,)), context)
    except GroundedExplanationValidationError as error:
        return error.reason, str(error)
    return None, None


def _san_tokens(text: str, facts: _ClaimEvidence) -> list[dict[str, Any]]:
    tokens = []
    for match in _SAN_TOKEN_RE.finditer(text):
        san = match.group("san")
        suffix = match.group("suffix") or ""
        legal = any(_parses(board, san) for board in facts.boards)
        correct = False
        for fact in facts.moves:
            try:
                parsed = fact.board.parse_san(san)
            except ValueError:
                continue
            if parsed != fact.move:
                continue
            if suffix == "#" and not fact.gives_mate:
                continue
            if suffix == "+" and not fact.gives_check:
                continue
            correct = True
            break
        tokens.append({"san": san + suffix, "legal": legal, "correct": correct})
    return tokens


def _parses(board: chess.Board, san: str) -> bool:
    try:
        board.parse_san(san)
    except ValueError:
        return False
    return True


_SAN_TOKEN_RE = re.compile(_SAN_TOKEN)


def _claim_record(
    claim: Any, context: GroundedExplanationContext, board: chess.Board
) -> dict[str, Any]:
    reason, message = _claim_reason(claim, context)
    evidence = {item.evidence_id: item for item in context.evidence}.get(claim.evidence_ref)
    san: list[dict[str, Any]] = []
    if evidence is not None:
        facts = _ClaimEvidence.build(
            board, evidence, context.verified_lines.get(claim.evidence_ref, ())
        )
        san = _san_tokens(claim.text, facts)
    return {
        "text": claim.text,
        "evidence_ref": claim.evidence_ref,
        "line": list(claim.line),
        "certainty": claim.certainty,
        "disclosure_level": claim.disclosure_level,
        "rejection_reason": reason,
        "rejection_message": message,
        "san_tokens": san,
    }


async def run_one(
    model: ModelEntry,
    position: Position,
    run_index: int,
    config: BenchConfig,
    entry: PipelineEntry,
    context_builder: ContextBuilder,
) -> dict[str, Any]:
    transport = _MeteredTransport(config.request_overrides)
    async with httpx.AsyncClient(transport=transport, timeout=config.request_timeout_s) as client:
        adapter = create_llm_provider(
            LLMProviderConfig(provider="ollama", model=model.served_name, base_url=model.base_url),
            client=client,
            environment={},
        )
        if adapter is None:
            raise RuntimeError("create_llm_provider returned no provider for an ollama config")
        provider = RecordingProvider(adapter)
        before = assembly.template_fallback_counts()
        explanation = await entry(
            position.diagnosis,
            list(position.evidence),
            position.fen,
            local_provider=provider,
            environment={},
        )
        after = assembly.template_fallback_counts()
    fallback = [reason for reason, count in after.items() if count > before.get(reason, 0)]
    if len(fallback) > 1:
        raise RuntimeError(f"More than one fallback recorded for one call: {fallback}")
    accepted = bool(explanation.claims)
    if accepted == bool(fallback):
        raise RuntimeError("Pipeline outcome and fallback counter disagree")
    context = context_builder(position.diagnosis, list(position.evidence), position.fen)
    board = chess.Board(context.fen)
    claims = (
        [_claim_record(claim, context, board) for claim in provider.generated.claims]
        if provider.generated is not None
        else []
    )
    body = transport.last_body or {}
    return {
        "model": model.name,
        "position_id": position.position_id,
        "code": position.code,
        "run": run_index,
        "outcome": "accepted" if accepted else "fallback",
        "fallback_reason": fallback[0] if fallback else None,
        "provider_error": provider.error,
        "latency_s": provider.latency_s,
        "prompt_chars": sum(len(m.content) for m in provider.request.messages)
        if provider.request is not None
        else None,
        "prompt_eval_count": body.get("prompt_eval_count"),
        "eval_count": body.get("eval_count"),
        "explanation_text": explanation.text,
        "claims": claims,
    }


def _nvidia_used_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return int(out.strip().splitlines()[0])


def _foreign_loaded(config: BenchConfig) -> list[str]:
    loaded: list[str] = []
    for url in config.foreign_ollama:
        try:
            response = httpx.get(f"{url}/api/ps", timeout=10)
        except httpx.ConnectError:
            continue
        loaded += [f"{url}:{item['name']}" for item in response.json().get("models", [])]
    return loaded


def _wait_for_free_gpu(config: BenchConfig) -> None:
    while loaded := _foreign_loaded(config):
        LOG.info("waiting: another Ollama holds the GPU: %s", loaded)
        time.sleep(30)


def _unload_all(models: Sequence[ModelEntry]) -> None:
    for url in sorted({model.base_url for model in models}):
        loaded = httpx.get(f"{url}/api/ps", timeout=30).json().get("models", [])
        for item in loaded:
            httpx.post(
                f"{url}/api/generate",
                json={"model": item["name"], "keep_alive": 0},
                timeout=60,
            ).raise_for_status()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if all(
            not httpx.get(f"{url}/api/ps", timeout=30).json().get("models")
            for url in {model.base_url for model in models}
        ):
            return
        time.sleep(1)
    raise RuntimeError("Ollama did not unload its models within 60 s")


async def measure_model(
    model: ModelEntry,
    positions: Sequence[Position],
    config: BenchConfig,
    sink: Path,
    done: set[tuple[str, str, int]],
    all_models: Sequence[ModelEntry],
) -> None:
    entry = cast(PipelineEntry, _resolve(config.pipeline_entry))
    context_builder = cast(ContextBuilder, _resolve(config.grounding_context))
    pending = [
        (position, run)
        for run in range(config.runs_per_position)
        for position in positions
        if (model.name, position.position_id, run) not in done
    ]
    if not pending:
        LOG.info("%s: nothing to do", model.name)
        return
    _unload_all(all_models)
    _wait_for_free_gpu(config)
    idle_mib = _nvidia_used_mib()
    started = time.perf_counter()
    warm = await run_one(model, positions[0], -1, config, entry, context_builder)
    load_s = time.perf_counter() - started
    loaded = httpx.get(f"{model.base_url}/api/ps", timeout=30).json()["models"]
    served = [item for item in loaded if item["name"].startswith(model.served_name)]
    if len(served) != 1:
        raise RuntimeError(f"{model.served_name} is not the one loaded model: {loaded}")
    peak_mib = _nvidia_used_mib()
    _append(
        sink,
        {
            "record": "model_load",
            "model": model.name,
            "load_and_first_call_s": load_s,
            "warmup_outcome": warm["outcome"],
            "ollama_size_bytes": served[0]["size"],
            "ollama_size_vram_bytes": served[0]["size_vram"],
            "ollama_context_length": served[0].get("context_length"),
            "gpu_idle_used_mib": idle_mib,
            "gpu_loaded_used_mib": peak_mib,
        },
    )
    for count, (position, run) in enumerate(pending, start=1):
        record = await run_one(model, position, run, config, entry, context_builder)
        used = _nvidia_used_mib()
        peak_mib = max(peak_mib, used)
        record["record"] = "run"
        record["gpu_used_mib"] = used
        record["foreign_models_loaded"] = _foreign_loaded(config)
        _append(sink, record)
        if count % 25 == 0 or count == len(pending):
            LOG.info("%s: %d/%d", model.name, count, len(pending))
    _append(
        sink,
        {
            "record": "model_done",
            "model": model.name,
            "gpu_peak_used_mib": peak_mib,
            "gpu_idle_used_mib": idle_mib,
        },
    )
    _unload_all(all_models)


def _append(sink: Path, record: dict[str, Any]) -> None:
    with sink.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def _read(sink: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines() if line]


# --- Summary -------------------------------------------------------------------


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty sequence")
    rank = fraction * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(records: Sequence[dict[str, Any]], config: BenchConfig) -> dict[str, Any]:
    header = next(record for record in records if record.get("record") == "header")
    by_model: dict[str, dict[str, Any]] = {}
    for model in config.models:
        runs = [r for r in records if r.get("record") == "run" and r["model"] == model.name]
        if not runs:
            continue
        load = next(
            r for r in records if r.get("record") == "model_load" and r["model"] == model.name
        )
        done = [r for r in records if r.get("record") == "model_done" and r["model"] == model.name]
        claims = [claim for run in runs for claim in run["claims"]]
        generated = [run for run in runs if run["provider_error"] is None]
        reasons = Counter(c["rejection_reason"] for c in claims if c["rejection_reason"])
        chess_false = [
            sum(1 for c in run["claims"] if c["rejection_reason"] in CHESS_FACT_REASONS)
            for run in generated
        ]
        rejected = [sum(1 for c in run["claims"] if c["rejection_reason"]) for run in generated]
        san = [token for claim in claims for token in claim["san_tokens"]]
        latencies = [run["latency_s"] for run in runs]
        by_code: dict[str, float] = {}
        for code in sorted({run["code"] for run in runs}):
            subset = [run for run in runs if run["code"] == code]
            by_code[code] = sum(r["outcome"] == "accepted" for r in subset) / len(subset)
        by_model[model.name] = {
            "role": model.role,
            "served_name": model.served_name,
            "params_b": model.params_b,
            "licence": model.licence,
            "runs": len(runs),
            "grounding_pass_rate": sum(r["outcome"] == "accepted" for r in runs) / len(runs),
            "grounding_pass_rate_by_code": by_code,
            "provider_error_rate": sum(r["provider_error"] is not None for r in runs) / len(runs),
            "fallback_reasons": dict(
                Counter(r["fallback_reason"] for r in runs if r["fallback_reason"])
            ),
            "claims_per_explanation": len(claims) / len(generated) if generated else None,
            "false_chess_statements_per_explanation": statistics.fmean(chess_false)
            if chess_false
            else None,
            "rejected_claims_per_explanation": statistics.fmean(rejected) if rejected else None,
            "claim_rejection_reasons": dict(reasons.most_common()),
            "san_tokens": len(san),
            "san_legal_rate": sum(t["legal"] for t in san) / len(san) if san else None,
            "san_correct_rate": sum(t["correct"] for t in san) / len(san) if san else None,
            "latency_p50_s": _percentile(latencies, 0.5),
            "latency_p95_s": _percentile(latencies, 0.95),
            "within_production_limit_rate": sum(
                lat <= config.production_time_limit_s for lat in latencies
            )
            / len(latencies),
            "load_and_first_call_s": load["load_and_first_call_s"],
            "vram_model_gib": load["ollama_size_vram_bytes"] / 2**30,
            "model_size_gib": load["ollama_size_bytes"] / 2**30,
            "gpu_peak_over_idle_gib": (done[0]["gpu_peak_used_mib"] - done[0]["gpu_idle_used_mib"])
            / 1024
            if done
            else None,
            "mean_prompt_tokens": statistics.fmean(
                r["prompt_eval_count"] for r in runs if r["prompt_eval_count"] is not None
            )
            if any(r["prompt_eval_count"] is not None for r in runs)
            else None,
            "mean_output_tokens": statistics.fmean(
                r["eval_count"] for r in runs if r["eval_count"] is not None
            )
            if any(r["eval_count"] is not None for r in runs)
            else None,
        }
    return {"header": header, "models": by_model}


def _fmt(value: float | None, pattern: str) -> str:
    return "—" if value is None else format(value, pattern)


def summary_markdown(summary: dict[str, Any]) -> str:
    header = summary["header"]
    lines = [
        f"Pipeline `{header['pipeline']['pipeline_entry']}` at commit "
        f"`{header['pipeline']['commit']}` (module sha256 "
        f"`{header['pipeline']['module_sha256'][:12]}`), {header['positions']} positions x "
        f"{header['runs_per_position']} runs, GPU {header['gpu']}, "
        f"Ollama {header['ollama_version']}.",
        "",
        "| Model | Role | Params (B) | Grounding pass | Provider errors | Claims / expl. "
        "| False chess statements / expl. | Rejected claims / expl. | SAN legal | SAN correct "
        "(SAN tokens) | Latency p50 / p95 (s) | Within 4 s | VRAM (GiB) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, row in summary["models"].items():
        lines.append(
            f"| {name} | {row['role']} | {row['params_b']} "
            f"| {row['grounding_pass_rate']:.1%} | {row['provider_error_rate']:.1%} "
            f"| {_fmt(row['claims_per_explanation'], '.2f')} "
            f"| {_fmt(row['false_chess_statements_per_explanation'], '.2f')} "
            f"| {_fmt(row['rejected_claims_per_explanation'], '.2f')} "
            f"| {_fmt(row['san_legal_rate'], '.1%')} "
            f"| {_fmt(row['san_correct_rate'], '.1%')} ({row['san_tokens']}) "
            f"| {row['latency_p50_s']:.2f} / {row['latency_p95_s']:.2f} "
            f"| {row['within_production_limit_rate']:.1%} | {row['vram_model_gib']:.2f} |"
        )
    return "\n".join(lines) + "\n"


def write_summary(sink: Path, config: BenchConfig) -> tuple[Path, Path]:
    summary = summarize(_read(sink), config)
    stamp = sink.stem.removeprefix("runs_")
    json_path = sink.with_name(f"summary_{stamp}.json")
    md_path = sink.with_name(f"summary_{stamp}.md")
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(summary_markdown(summary), encoding="utf-8")
    return json_path, md_path


def _gpu_name() -> str:
    return subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def run(
    config: BenchConfig, sink: Path, selected: Sequence[ModelEntry], limit: int | None
) -> None:
    positions = load_positions(config.positions_file)[:limit]
    identity = pipeline_identity(config)
    if sink.exists():
        records = _read(sink)
        header = next(r for r in records if r.get("record") == "header")
        if header["pipeline"] != identity:
            raise RuntimeError(
                f"Resume refused: pipeline changed since the run started: "
                f"{header['pipeline']} != {identity}"
            )
        done = {
            (r["model"], r["position_id"], r["run"]) for r in records if r.get("record") == "run"
        }
    else:
        versions = {
            httpx.get(f"{url}/api/version", timeout=30).json()["version"]
            for url in {model.base_url for model in config.models}
        }
        _append(
            sink,
            {
                "record": "header",
                "started": datetime.now(UTC).isoformat(),
                "pipeline": identity,
                "positions_file": config.positions_file.relative_to(REPO_ROOT).as_posix(),
                "positions_sha256": hashlib.sha256(config.positions_file.read_bytes()).hexdigest(),
                "positions": len(positions),
                "runs_per_position": config.runs_per_position,
                "num_ctx": config.num_ctx,
                "num_predict": config.num_predict,
                "request_overrides": config.request_overrides,
                "request_timeout_s": config.request_timeout_s,
                "gpu": _gpu_name(),
                "ollama_version": ",".join(sorted(versions)),
                "python": sys.version.split()[0],
            },
        )
        done = set()
    for model in selected:
        LOG.info("measuring %s", model.name)
        await measure_model(model, positions, config, sink, done, config.models)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--setup", action="store_true", help="pull/download/create models first")
    parser.add_argument("--setup-only", action="store_true", help="run --setup, then stop")
    parser.add_argument("--limit", type=int, help="smoke test: first N positions only")
    parser.add_argument("--ollama-binary", default="ollama")
    parser.add_argument("--models", nargs="*", help="subset of model names (default: all)")
    parser.add_argument("--resume", type=Path, help="append to an existing runs_<stamp>.jsonl")
    parser.add_argument("--summarize", type=Path, help="only rewrite the summary of a runs file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = load_config(args.config)
    if args.summarize is not None:
        for path in write_summary(args.summarize, config):
            print(f"wrote {path}")
        return
    selected = [
        model for model in config.models if not args.models or model.name in set(args.models)
    ]
    if args.models and len(selected) != len(set(args.models)):
        raise SystemExit(f"Unknown model in {args.models}")
    if args.setup or args.setup_only:
        setup_models(config, args.ollama_binary)
        if args.setup_only:
            return
    config.results_dir.mkdir(parents=True, exist_ok=True)
    sink = args.resume or config.results_dir / (
        f"runs_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.jsonl"
    )
    LOG.info("results: %s", sink)
    asyncio.run(run(config, sink, selected, args.limit))
    for path in write_summary(sink, config):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
