"""Benchmark open LLMs as the Scan64 narrator (issue #369).

Usage (repository root, Ollama serving at each model's ``base_url``):
    uv run python -m benchmarks.narrator.run_benchmark benchmarks/narrator/narrator.toml --setup
    uv run python -m benchmarks.narrator.run_benchmark CONFIG --models praxis-4b qwen3.5-4b
    uv run python -m benchmarks.narrator.run_benchmark CONFIG --resume RUNS.jsonl
    uv run python -m benchmarks.narrator.run_benchmark CONFIG --summarize RUNS.jsonl
    uv run python -m benchmarks.narrator.run_benchmark CONFIG --voice terse \
        --positions-file benchmarks/narrator/positions-caissa.json --num-ctx 4096

Every generation goes through the production path: the configured
``pipeline_entry`` (default ``resolve_explanation``) with an
``OllamaExplanationAdapter`` built by ``create_llm_provider`` as the local
provider. The adapter is wrapped only to time the call and keep its output.
The pipeline's own validator decides acceptance; the accepted/fallback outcome
and the fallback reason come from the pipeline (``template_fallback_counts``).
Each claim is then validated on its own against the same grounding context to
count rejected claims per explanation and their reasons.

One JSON line per (model, position, run) is appended to ``runs_<stamp>.jsonl``
as soon as it finishes; ``--resume`` skips lines already present and refuses a
run whose commit, GPU, Ollama version, positions file, voice or ``num_ctx``
differs from the run header. The summary
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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import chess
import httpx
from chess_lesson_spec import Diagnosis, Explanation

from scan64.explanations import assembly
from scan64.explanations.slots import slot_names
from scan64.explanations.validator import (
    _SAN_TOKEN,
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    _ClaimEvidence,
    validate_generated_explanation,
)
from scan64.explanations.voice import NarratorVoice, parse_narrator_voice
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
        # Slot path (#386): chess content written as prose instead of a slot.
        "chess_token_outside_slot",
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
    # Narrator voice passed to the pipeline entry (#387); "coach" when unset.
    voice: NarratorVoice
    positions_file: Path
    models: tuple[ModelEntry, ...]
    # The TOML file this config was read from; its digest is in the run header.
    config_file: Path


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
        voice=parse_narrator_voice(run.get("voice")),
        positions_file=REPO_ROOT / run.get("positions_file", raw["positions"]["file"]),
        models=models,
        config_file=path.resolve(),
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


def _config_label(path: Path) -> str:
    """Repository-relative path of a config file, or its name outside the repository."""
    return path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else path.name


def narrator_package_files(source_file: Path) -> list[Path]:
    """Every file of the package that holds the pipeline entry, sorted by path.

    For ``scan64.explanations.assembly`` that is ``src/scan64/explanations``:
    assembly, slots, facts, factor_facts, validator, board_lines, voice, the
    factor port and the templates. Compiled caches are left out.
    """
    package = source_file.parent
    return sorted(
        path
        for path in package.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )


def narrator_digest(source_file: Path) -> tuple[str, dict[str, str]]:
    """sha256 over the package's files (path and content digest per line), and the per-file map."""
    # Repository-relative names; a package outside the repository is named from its parent.
    base = REPO_ROOT if source_file.is_relative_to(REPO_ROOT) else source_file.parent.parent
    modules = {
        path.relative_to(base).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in narrator_package_files(source_file)
    }
    combined = hashlib.sha256(
        "".join(f"{name}\0{digest}\n" for name, digest in modules.items()).encode("utf-8")
    ).hexdigest()
    return combined, modules


def pipeline_identity(config: BenchConfig) -> dict[str, Any]:
    """Pin the code under test: commit, whole-tree dirty flag, module, runner and config hashes.

    ``dirty`` covers the whole tree except the results directory, so uncommitted
    runner or config changes are flagged, not only changes under ``src``. The
    config digest also covers a ``--config`` file outside the repository, which
    ``dirty`` cannot see. ``module_sha256`` covers the entry's own file only;
    ``narrator_sha256`` covers every file of its package (``narrator_modules``
    lists them), so a change to slots, facts, factor_facts or the validator
    changes the header too.
    """
    entry = _resolve(config.pipeline_entry)
    source_file = Path(inspect.getfile(entry))
    results = config.results_dir.relative_to(REPO_ROOT).as_posix()
    narrator_sha256, narrator_modules = narrator_digest(source_file)
    return {
        "pipeline_entry": config.pipeline_entry,
        "grounding_context": config.grounding_context,
        "commit": _git("rev-parse", "HEAD"),
        "module_commit": _git("log", "-1", "--format=%H", "--", str(source_file)),
        "dirty": bool(_git("status", "--porcelain", "--", ".", f":(exclude){results}")),
        "module_file": source_file.relative_to(REPO_ROOT).as_posix(),
        "module_sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
        "narrator_sha256": narrator_sha256,
        "narrator_modules": narrator_modules,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "config_file": _config_label(config.config_file),
        "config_sha256": hashlib.sha256(config.config_file.read_bytes()).hexdigest(),
    }


RESUME_PINNED = (
    "pipeline_entry",
    "grounding_context",
    "commit",
    "module_file",
    "module_sha256",
    "narrator_sha256",
    "runner_sha256",
    "config_sha256",
)


def refuse_resume(header: dict[str, Any], identity: dict[str, Any]) -> None:
    """Refuse to append to a run whose header no longer describes the code (rule 6).

    New records sit under the existing header, so the run must have started from a
    clean tree, the tree must be clean now, and the commit, module, narrator package, runner and
    config file digest must equal the header's. A header without one of these keys
    (written by an older runner) cannot be resumed.
    """
    if identity["dirty"]:
        raise RuntimeError("Resume refused: the working tree has uncommitted changes")
    if header["pipeline"].get("dirty") is not False:
        raise RuntimeError("Resume refused: the run started from a dirty or unrecorded tree")
    changed = [key for key in RESUME_PINNED if header["pipeline"].get(key) != identity[key]]
    if changed:
        raise RuntimeError(
            f"Resume refused: {', '.join(changed)} changed since the run started: "
            f"{header['pipeline']} != {identity}"
        )


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


def setup_models(
    config: BenchConfig, ollama_binary: str, models: Sequence[ModelEntry] | None = None
) -> None:
    """Create one Ollama model per entry from its Modelfile (idempotent).

    ``models`` limits setup to a subset (``--models``); default every entry.
    """
    build_dir = config.gguf_cache / "modelfiles"
    build_dir.mkdir(parents=True, exist_ok=True)
    for model in config.models if models is None else models:
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
    counts Ollama reports (the adapter discards them).

    With ``stream = true`` in the overrides, Ollama streams NDJSON chunks. The
    transport records the time to the first content token and joins the chunks
    into the one non-streamed reply body the production adapter parses, so the
    parse path is the same either way. It also keeps any thinking text: the
    benchmark sends ``think = false`` and reports every call that still thinks.
    """

    def __init__(self, overrides: dict[str, Any]) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self._overrides = overrides
        self.last_body: dict[str, Any] | None = None
        self.first_token_s: float | None = None
        self.thinking_chars = 0

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
        started = time.perf_counter()
        response = await self._inner.handle_async_request(request)
        if self._overrides.get("stream") is True and request.url.path.endswith("/api/chat"):
            content = await self._join_stream(response, started)
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in ("content-length", "transfer-encoding", "content-type")
            }
            headers["content-type"] = "application/json"
        else:
            content = await response.aread()
            headers = dict(response.headers)
        try:
            reply: object = json.loads(content)
        except json.JSONDecodeError:
            reply = None
        self.last_body = cast(dict[str, Any], reply) if isinstance(reply, dict) else None
        if self.last_body is not None:
            message = self.last_body.get("message")
            thinking = message.get("thinking") if isinstance(message, dict) else None
            if isinstance(thinking, str):
                self.thinking_chars = len(thinking)
        return httpx.Response(
            status_code=response.status_code,
            headers=headers,
            content=content,
            request=request,
        )

    async def _join_stream(self, response: httpx.Response, started: float) -> bytes:
        if response.status_code != 200:
            return await response.aread()
        parts: list[str] = []
        thinking: list[str] = []
        final: dict[str, Any] = {}
        buffer = b""
        async for raw in response.aiter_raw():
            buffer += raw
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                message = chunk.get("message") or {}
                if message.get("content"):
                    if self.first_token_s is None:
                        self.first_token_s = time.perf_counter() - started
                    parts.append(message["content"])
                if message.get("thinking"):
                    thinking.append(message["thinking"])
                if chunk.get("done"):
                    final = chunk
        await response.aclose()
        joined = {key: value for key, value in final.items() if key != "message"}
        joined["message"] = {
            "role": "assistant",
            "content": "".join(parts),
            **({"thinking": "".join(thinking)} if thinking else {}),
        }
        return json.dumps(joined).encode()

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
    except Exception as error:
        # Not a rejection the validator defines: it escapes the pipeline.
        return "validator_exception", f"{type(error).__name__}: {error}"
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


def _claim_slots(claim: Any, context: GroundedExplanationContext) -> list[dict[str, Any]]:
    """The slots a claim names, with the code-rendered value (``None``: not listed)."""
    if context.slots is None:
        return []
    listed = context.slots.slots_for(claim.evidence_ref)
    records = []
    for name in slot_names(claim.text):
        slot = listed.get(name)
        records.append(
            {
                "evidence_ref": claim.evidence_ref,
                "name": name,
                "value": slot.value if slot is not None else None,
                "moves": list(slot.moves) if slot is not None else [],
            }
        )
    return records


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
        "slots": _claim_slots(claim, context),
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
            LLMProviderConfig(
                provider="ollama",
                model=model.served_name,
                base_url=model.base_url,
                context_tokens=config.num_ctx,
            ),
            client=client,
            environment={},
        )
        if adapter is None:
            raise RuntimeError("create_llm_provider returned no provider for an ollama config")
        provider = RecordingProvider(adapter)
        before = assembly.template_fallback_counts()
        # A pipeline exception is a measured outcome: the app would surface it
        # instead of the template (see the report, validator `_tactic` defect).
        explanation: Explanation | None = None
        pipeline_exception: str | None = None
        try:
            explanation = await entry(
                position.diagnosis,
                list(position.evidence),
                position.fen,
                local_provider=provider,
                environment={},
                voice=config.voice,
            )
        except Exception as error:
            pipeline_exception = f"{type(error).__name__}: {error}; cause={error.__cause__!r}"
        after = assembly.template_fallback_counts()
    fallback = [reason for reason, count in after.items() if count > before.get(reason, 0)]
    if len(fallback) > 1:
        raise RuntimeError(f"More than one fallback recorded for one call: {fallback}")
    accepted = explanation is not None and bool(explanation.claims)
    if explanation is not None and accepted == bool(fallback):
        raise RuntimeError("Pipeline outcome and fallback counter disagree")
    if explanation is None:
        outcome = "pipeline_exception"
    else:
        outcome = "accepted" if accepted else "fallback"
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
        "outcome": outcome,
        "pipeline_exception": pipeline_exception,
        "fallback_reason": fallback[0] if fallback else None,
        "provider_error": provider.error,
        "latency_s": provider.latency_s,
        "prompt_chars": sum(len(m.content) for m in provider.request.messages)
        if provider.request is not None
        else None,
        "prompt_eval_count": body.get("prompt_eval_count"),
        "eval_count": body.get("eval_count"),
        "done_reason": body.get("done_reason"),
        "first_token_s": transport.first_token_s,
        "thinking_chars": transport.thinking_chars,
        "think_tag_in_content": "<think>" in str((body.get("message") or {}).get("content", "")),
        "explanation_text": explanation.text if explanation is not None else None,
        "claims": claims,
        # Every slot the generated claims name, in claim order (#386). Present
        # only in records of the slot pipeline; coverage counts from it.
        **(
            {"slots_used": [slot for claim in claims for slot in claim["slots"]]}
            if context.slots is not None
            else {}
        ),
    }


def _nvidia_smi(*query: str) -> str | None:
    """Run nvidia-smi; None when it is absent or fails (CPU or non-NVIDIA machine)."""
    try:
        result = subprocess.run(["nvidia-smi", *query], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _nvidia_used_mib() -> int | None:
    out = _nvidia_smi("--query-gpu=memory.used", "--format=csv,noheader,nounits")
    return None if out is None else int(out.splitlines()[0])


def _gib_over(high: int | None, low: int | None) -> float | None:
    return None if high is None or low is None else (high - low) / 1024


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
            # Pins library models, which are named only by a mutable Ollama tag.
            "ollama_digest": served[0].get("digest"),
            "gpu_idle_used_mib": idle_mib,
            "gpu_loaded_used_mib": peak_mib,
        },
    )
    for count, (position, run) in enumerate(pending, start=1):
        record = await run_one(model, position, run, config, entry, context_builder)
        used = _nvidia_used_mib()
        if used is not None:
            peak_mib = used if peak_mib is None else max(peak_mib, used)
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


# --- Coverage --------------------------------------------------------------------
#
# The validator rejects false claims but asks for no content, so an answer with
# one vague true claim passes. Coverage counts how many of three key engine facts
# an accepted answer states: the played move, the engine's key move, and the
# result (piece, check, mate or tactic). A move counts in UCI, in SAN, or in the
# claim's own line. Records of the slot pipeline (#386) carry ``slots_used`` and
# are counted from the slots instead (``slot_coverage``); older records keep the
# claim-text count, so their summaries do not change.

_PIECE_NAMES = {"p": "pawn", "n": "knight", "b": "bishop", "r": "rook", "q": "queen", "k": "king"}
_TACTIC_NAMES = {
    "knight_fork": ("fork",),
    "fork": ("fork",),
    "pin": ("pin",),
    "skewer": ("skewer",),
    "discovered_attack": ("discovered",),
    "back_rank": ("back rank", "back-rank"),
    "overloaded_defender": ("overload",),
}


@dataclass(frozen=True)
class KeyFacts:
    played: tuple[str, ...]
    key_move: tuple[str, ...]
    result: tuple[str, ...]


def _move_forms(fen: str, uci: str) -> tuple[str, ...]:
    forms = [uci]
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        if move in board.legal_moves:
            forms.append(board.san(move).rstrip("+#"))
    except ValueError:
        pass
    return tuple(forms)


def key_facts(payload: dict[str, Any]) -> KeyFacts:
    """The three facts a useful narration of this evidence states."""
    before, after = payload["before_fen"], payload["after_fen"]
    played_san = str(payload["played_move"]).rstrip("+#")
    played: tuple[str, ...] = (played_san,)
    board = chess.Board(before)
    try:
        played = (played_san, board.parse_san(played_san).uci())
    except ValueError:
        pass
    if payload.get("best_move"):
        key_move = _move_forms(before, payload["best_move"])
    elif payload.get("threat_move"):
        key_move = _move_forms(after, payload["threat_move"])
    elif payload.get("mating_line"):
        key_move = _move_forms(after, payload["mating_line"][0])
    else:
        key_move = _move_forms(after, payload["proving_line"][0])
    if payload.get("mate_in"):
        result: tuple[str, ...] = ("mate",)
    elif payload.get("tactic_type"):
        result = _TACTIC_NAMES.get(payload["tactic_type"], (str(payload["tactic_type"]),))
    else:
        piece = next(
            (
                payload[k]
                for k in ("captured_piece", "threatened_piece", "hanging_piece")
                if payload.get(k)
            ),
            None,
        )
        result = (_PIECE_NAMES[str(piece).lower()],) if piece else ("check",)
    return KeyFacts(played=played, key_move=key_move, result=result)


def _states_move(forms: tuple[str, ...], text: str, lines: list[str]) -> bool:
    return any(
        form in lines or re.search(rf"(?<![A-Za-z0-9]){re.escape(form)}(?![A-Za-z0-9])", text)
        for form in forms
    )


def coverage(claims: Sequence[dict[str, Any]], facts: KeyFacts) -> float:
    """Share of the three key facts that the claims state."""
    text = " ".join(claim["text"] for claim in claims)
    lines = [move for claim in claims for move in claim.get("line", [])]
    lowered = text.lower()
    stated = (
        _states_move(facts.played, text, lines),
        _states_move(facts.key_move, text, lines),
        any(re.search(rf"\b{re.escape(word)}", lowered) for word in facts.result),
    )
    return sum(stated) / len(stated)


def slot_coverage(slots: Sequence[dict[str, Any]], facts: KeyFacts) -> float:
    """Share of the three key facts stated by the slots an answer uses (#386).

    A move counts when a used slot states it (its UCI moves or its rendered
    SAN); the result counts when a used slot's rendered value names it. Prose
    outside the slots holds no chess content, so it adds nothing.
    """
    listed = [slot for slot in slots if slot.get("value") is not None]
    text = " ".join(str(slot["value"]) for slot in listed)
    moves = [move for slot in listed for move in slot.get("moves", [])]
    lowered = text.lower()
    stated = (
        _states_move(facts.played, text, moves),
        _states_move(facts.key_move, text, moves),
        any(re.search(rf"\b{re.escape(word)}", lowered) for word in facts.result),
    )
    return sum(stated) / len(stated)


def run_coverage(run: dict[str, Any], facts: KeyFacts) -> float:
    """Slot coverage for slot-pipeline records; claim-text coverage for older records."""
    if "slots_used" in run:
        return slot_coverage(run["slots_used"], facts)
    return coverage(run["claims"], facts)


# --- Factor metric (#410) ------------------------------------------------------------
#
# Share of accepted explanations that name at least one measured factor change:
# a used ``{factor:...}`` slot that the slot list held (its rendered value is
# set). Only slot-pipeline records (``slots_used``) can name a factor slot; for
# older records the metric is ``None``. ``factor_named_where_available`` divides
# by the accepted answers whose position has a factor slot under the configured
# grounding context, so a position with no measured change does not count
# against the model.

FACTOR_SLOT_PREFIX = "factor:"


def names_factor(run: dict[str, Any]) -> bool | None:
    """True when the run uses a listed factor slot; ``None`` for pre-slot records."""
    if "slots_used" not in run:
        return None
    return any(
        str(slot["name"]).startswith(FACTOR_SLOT_PREFIX) and slot.get("value") is not None
        for slot in run["slots_used"]
    )


def factor_positions(config: BenchConfig) -> frozenset[str]:
    """Positions whose slot list holds at least one factor slot."""
    builder = cast(ContextBuilder, _resolve(config.grounding_context))
    found = set()
    for position in load_positions(config.positions_file):
        context = builder(position.diagnosis, list(position.evidence), position.fen)
        if context.slots is not None and any(
            name.startswith(FACTOR_SLOT_PREFIX) for name in context.slots.names()
        ):
            found.add(position.position_id)
    return frozenset(found)


def factor_metric(
    accepted_runs: Sequence[dict[str, Any]], with_factor: frozenset[str]
) -> dict[str, float | None]:
    named = [(run, flag) for run in accepted_runs if (flag := names_factor(run)) is not None]
    available = [flag for run, flag in named if run["position_id"] in with_factor]
    return {
        "factor_named_accepted": sum(flag for _, flag in named) / len(named) if named else None,
        "factor_named_where_available": sum(available) / len(available) if available else None,
    }


def _position_facts(config: BenchConfig) -> dict[str, KeyFacts]:
    raw = json.loads(config.positions_file.read_text(encoding="utf-8"))
    return {
        item["position_id"]: key_facts(item["evidence"][0]["payload"]) for item in raw["positions"]
    }


def summarize(records: Sequence[dict[str, Any]], config: BenchConfig) -> dict[str, Any]:
    header = next(record for record in records if record.get("record") == "header")
    facts = _position_facts(config)
    with_factor = factor_positions(config)
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
        prompt_tokens = [r["prompt_eval_count"] for r in runs if r["prompt_eval_count"] is not None]
        accepted_runs = [run for run in runs if run["outcome"] == "accepted"]
        covered = [run_coverage(run, facts[run["position_id"]]) for run in accepted_runs]
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
            # Mean share of the three key facts in accepted answers, and the same
            # share over all calls with a fallback counted as 0: what a user gets.
            "coverage_accepted": statistics.fmean(covered) if covered else None,
            "delivered_coverage": sum(covered) / len(runs),
            "full_coverage_rate": sum(value == 1 for value in covered) / len(runs),
            # #410: accepted answers that name a measured factor change by slot.
            **factor_metric(accepted_runs, with_factor),
            "provider_error_rate": sum(r["provider_error"] is not None for r in runs) / len(runs),
            "pipeline_exception_rate": sum(r["outcome"] == "pipeline_exception" for r in runs)
            / len(runs),
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
            "first_token_p50_s": _percentile(
                [r["first_token_s"] for r in runs if r.get("first_token_s") is not None], 0.5
            )
            if any(r.get("first_token_s") is not None for r in runs)
            else None,
            "length_stop_rate": sum(r.get("done_reason") == "length" for r in runs) / len(runs),
            "thinking_calls": sum(
                bool(r.get("thinking_chars")) or bool(r.get("think_tag_in_content")) for r in runs
            ),
            "latency_p95_s": _percentile(latencies, 0.95),
            "within_production_limit_rate": sum(
                lat <= config.production_time_limit_s for lat in latencies
            )
            / len(latencies),
            "load_and_first_call_s": load["load_and_first_call_s"],
            # Measured GPU memory rise when the model loads (weights, KV cache and
            # buffers). Ollama's size_vram under-reports some models (gemma4).
            # None without nvidia-smi (CPU or non-NVIDIA machine).
            "vram_model_gib": _gib_over(load["gpu_loaded_used_mib"], load["gpu_idle_used_mib"]),
            "ollama_size_vram_gib": load["ollama_size_vram_bytes"] / 2**30,
            "model_size_gib": load["ollama_size_bytes"] / 2**30,
            "gpu_peak_over_idle_gib": _gib_over(
                done[0]["gpu_peak_used_mib"], done[0]["gpu_idle_used_mib"]
            )
            if done
            else None,
            "mean_prompt_tokens": statistics.fmean(
                r["prompt_eval_count"] for r in runs if r["prompt_eval_count"] is not None
            )
            if any(r["prompt_eval_count"] is not None for r in runs)
            else None,
            # Prompt tokens per call: the num_ctx a model needs is max + num_predict.
            "median_prompt_tokens": statistics.median(prompt_tokens) if prompt_tokens else None,
            "max_prompt_tokens": max(prompt_tokens) if prompt_tokens else None,
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
        f"{header['runs_per_position']} runs, GPU {header['gpu'] or 'none (no nvidia-smi)'}, "
        f"Ollama {header['ollama_version']}"
        + (f", `num_ctx` {header['num_ctx']}." if "num_ctx" in header else ".")
        + (f" Positions `{header['positions_file']}`." if "positions_file" in header else "")
        # Config digest; headers before it have none and keep their summary text.
        + (
            f" Config `{header['pipeline']['config_file']}` (sha256 "
            f"`{header['pipeline']['config_sha256'][:12]}`)."
            if "config_sha256" in header["pipeline"]
            else ""
        )
        # Narrator package digest; headers before it have none and keep their summary text.
        + (
            f" Narrator package sha256 `{header['pipeline']['narrator_sha256'][:12]}` "
            f"({len(header['pipeline'].get('narrator_modules', {}))} files)."
            if "narrator_sha256" in header["pipeline"]
            else ""
        )
        # Voice (#387); headers before #387 have none and keep their summary text.
        + (f" Narrator voice `{header['voice']}`." if "voice" in header else ""),
        "",
        "Coverage: share of three key engine facts (played move, key move, result) that "
        "an accepted answer states. Delivered coverage counts a fallback as 0. Full "
        "coverage: share of all calls accepted with all three facts. Names a factor: share of "
        "accepted answers that use at least one listed factor slot (a measured factor "
        "change), over all accepted answers and over those whose position has a factor slot.",
        "",
        "| Model | Role | Params (B) | Grounding pass | Coverage (accepted) "
        "| Delivered coverage | Full coverage | Names a factor (accepted / where available) "
        "| Provider errors | Claims / expl. "
        "| False chess statements / expl. | Rejected claims / expl. | SAN legal | SAN correct "
        "(SAN tokens) | Latency p50 / p95 (s) | First token p50 (s) | Within 4 s "
        "| Hit output cap | Calls that thought | Prompt tokens median / max | GPU memory (GiB) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, row in summary["models"].items():
        lines.append(
            f"| {name} | {row['role']} | {row['params_b']} "
            f"| {row['grounding_pass_rate']:.1%} "
            f"| {_fmt(row['coverage_accepted'], '.1%')} | {row['delivered_coverage']:.1%} "
            f"| {row['full_coverage_rate']:.1%} "
            f"| {_fmt(row.get('factor_named_accepted'), '.1%')} / "
            f"{_fmt(row.get('factor_named_where_available'), '.1%')} "
            f"| {row['provider_error_rate']:.1%} "
            f"| {_fmt(row['claims_per_explanation'], '.2f')} "
            f"| {_fmt(row['false_chess_statements_per_explanation'], '.2f')} "
            f"| {_fmt(row['rejected_claims_per_explanation'], '.2f')} "
            f"| {_fmt(row['san_legal_rate'], '.1%')} "
            f"| {_fmt(row['san_correct_rate'], '.1%')} ({row['san_tokens']}) "
            f"| {row['latency_p50_s']:.2f} / {row['latency_p95_s']:.2f} "
            f"| {_fmt(row.get('first_token_p50_s'), '.2f')} "
            f"| {row['within_production_limit_rate']:.1%} "
            f"| {row.get('length_stop_rate', 0):.1%} | {row.get('thinking_calls', 0)} "
            f"| {_fmt(row.get('median_prompt_tokens'), '.0f')} / "
            f"{_fmt(row.get('max_prompt_tokens'), '.0f')} "
            f"| {_fmt(row['vram_model_gib'], '.2f')} |"
        )
    return "\n".join(lines) + "\n"


def write_summary(sink: Path, config: BenchConfig) -> tuple[Path, Path]:
    records = _read(sink)
    header = next(record for record in records if record.get("record") == "header")
    # The run's own positions file decides the key facts, whatever the config says.
    if "positions_file" in header:
        config = replace(config, positions_file=REPO_ROOT / header["positions_file"])
    summary = summarize(records, config)
    stamp = sink.stem.removeprefix("runs_")
    json_path = sink.with_name(f"summary_{stamp}.json")
    md_path = sink.with_name(f"summary_{stamp}.md")
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(summary_markdown(summary), encoding="utf-8")
    return json_path, md_path


def _gpu_name() -> str | None:
    return _nvidia_smi("--query-gpu=name,memory.total", "--format=csv,noheader")


def _ollama_versions(config: BenchConfig) -> str:
    versions = {
        httpx.get(f"{url}/api/version", timeout=30).json()["version"]
        for url in {model.base_url for model in config.models}
    }
    return ",".join(sorted(versions))


async def run(
    config: BenchConfig, sink: Path, selected: Sequence[ModelEntry], limit: int | None
) -> None:
    positions = load_positions(config.positions_file)[:limit]
    identity = pipeline_identity(config)
    if sink.exists():
        records = _read(sink)
        header = next(r for r in records if r.get("record") == "header")
        refuse_resume(header, identity)
        # Runs before #387 have no voice field: their prompt had no voice line.
        if header.get("voice") != config.voice:
            raise RuntimeError(f"Resume refused: voice {config.voice!r} != {header.get('voice')!r}")
        # #380 review: a resumed run must measure the same code on the same
        # hardware and server, or its rows are not one measurement.
        current = {
            "commit": identity["commit"],
            "gpu": _gpu_name(),
            "ollama_version": _ollama_versions(config),
            "positions_sha256": hashlib.sha256(config.positions_file.read_bytes()).hexdigest(),
            "num_ctx": config.num_ctx,
        }
        recorded = {
            "commit": header["pipeline"]["commit"],
            **{key: header.get(key) for key in current if key != "commit"},
        }
        changed = {
            key: (recorded[key], current[key]) for key in current if recorded[key] != current[key]
        }
        if changed:
            raise RuntimeError(
                f"Resume refused: changed since the run started (header, now): {changed}"
            )
        done = {
            (r["model"], r["position_id"], r["run"]) for r in records if r.get("record") == "run"
        }
    else:
        _append(
            sink,
            {
                "record": "header",
                "started": datetime.now(UTC).isoformat(),
                "pipeline": identity,
                "voice": config.voice,
                "positions_file": config.positions_file.relative_to(REPO_ROOT).as_posix(),
                "positions_sha256": hashlib.sha256(config.positions_file.read_bytes()).hexdigest(),
                "positions": len(positions),
                "runs_per_position": config.runs_per_position,
                "num_ctx": config.num_ctx,
                "num_predict": config.num_predict,
                "request_overrides": config.request_overrides,
                "request_timeout_s": config.request_timeout_s,
                "gpu": _gpu_name(),
                "ollama_version": _ollama_versions(config),
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
    parser.add_argument("--voice", help="override [run].voice (coach, terse, playful)")
    parser.add_argument("--positions-file", type=Path, help="override [run].positions_file")
    parser.add_argument("--num-ctx", type=int, help="override [run].num_ctx (sent per request)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = load_config(args.config)
    if args.voice is not None:
        config = replace(config, voice=parse_narrator_voice(args.voice))
    if args.positions_file is not None:
        config = replace(config, positions_file=(REPO_ROOT / args.positions_file).resolve())
    if args.num_ctx is not None:
        config = replace(config, num_ctx=args.num_ctx)
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
        setup_models(config, args.ollama_binary, selected)
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
