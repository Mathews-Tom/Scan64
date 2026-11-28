"""Private, bounded Ollama transport. No runtime destination configuration.

The destination is fixed. The time and size bounds of one explanation are
configurable through the environment (documented in docs/narrator-pipeline.md):

| Variable | Default | Bound |
|---|---|---|
| ``SCAN64_LOCAL_LLM_TIMEOUT_SECONDS`` | 180 | model listing plus chat, one deadline |
| ``SCAN64_LOCAL_LLM_MAX_REQUEST_BYTES`` | 262144 (256 KiB) | serialised chat request |
| ``SCAN64_LOCAL_LLM_MAX_RESPONSE_BYTES`` | 262144 (256 KiB) | each response body |
| ``SCAN64_LOCAL_LLM_CONTEXT_TOKENS`` | 4096 | ``num_ctx``; a prompt filling it is rejected |
| ``SCAN64_LOCAL_LLM_MAX_CONCURRENT_CALLS`` | 2 | explanations in flight across the process |

A call over the process-wide limit fails at once with ``LLMProviderError``, so the
caller uses the template: there is no queue (#472).

The installation probe (``detect_local_ollama``) sends no evidence and keeps
its fixed 4 s deadline. An invalid value raises ``LLMConfigurationError``.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TypeGuard

import httpx

from scan64.providers.llm.adapters import (
    DEFAULT_OLLAMA_CONTEXT_TOKENS,
    MAX_OLLAMA_CONTEXT_TOKENS,
    MIN_OLLAMA_CONTEXT_TOKENS,
    LLMProviderError,
    OllamaExplanationAdapter,
)
from scan64.providers.llm.config import LLMConfigurationError
from scan64.providers.llm.contracts import ExplanationRequest, GeneratedExplanation

LOCAL_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_PROBE_SECONDS = 4.0
TIMEOUT_ENV = "SCAN64_LOCAL_LLM_TIMEOUT_SECONDS"
MAX_REQUEST_BYTES_ENV = "SCAN64_LOCAL_LLM_MAX_REQUEST_BYTES"
MAX_RESPONSE_BYTES_ENV = "SCAN64_LOCAL_LLM_MAX_RESPONSE_BYTES"
CONTEXT_TOKENS_ENV = "SCAN64_LOCAL_LLM_CONTEXT_TOKENS"
MAX_CONCURRENT_CALLS_ENV = "SCAN64_LOCAL_LLM_MAX_CONCURRENT_CALLS"
DEFAULT_MAX_CONCURRENT_CALLS = 2
_MAX_CONCURRENT_CALLS = 8
DEFAULT_OPERATION_SECONDS = 180.0
DEFAULT_MAX_REQUEST_BYTES = 256 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_CONTEXT_TOKENS = DEFAULT_OLLAMA_CONTEXT_TOKENS
_MIN_CONTEXT_TOKENS = MIN_OLLAMA_CONTEXT_TOKENS
_MAX_CONTEXT_TOKENS = MAX_OLLAMA_CONTEXT_TOKENS
_MAX_OPERATION_SECONDS = 600.0
_MIN_BYTES = 1024
_MAX_BYTES = 16 * 1024 * 1024
_MAX_MODELS = 256
_MAX_MODEL_LENGTH = 128
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z")


@dataclass(frozen=True)
class LocalOllamaLimits:
    """Time and size bounds for one local explanation (listing plus chat)."""

    operation_seconds: float = DEFAULT_OPERATION_SECONDS
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
    max_concurrent_calls: int = DEFAULT_MAX_CONCURRENT_CALLS

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> LocalOllamaLimits:
        values = os.environ if environment is None else environment
        return cls(
            operation_seconds=_seconds(values, TIMEOUT_ENV, DEFAULT_OPERATION_SECONDS),
            max_request_bytes=_byte_count(values, MAX_REQUEST_BYTES_ENV, DEFAULT_MAX_REQUEST_BYTES),
            max_response_bytes=_byte_count(
                values, MAX_RESPONSE_BYTES_ENV, DEFAULT_MAX_RESPONSE_BYTES
            ),
            context_tokens=_bounded_int(
                values,
                CONTEXT_TOKENS_ENV,
                DEFAULT_CONTEXT_TOKENS,
                _MIN_CONTEXT_TOKENS,
                _MAX_CONTEXT_TOKENS,
            ),
            max_concurrent_calls=_bounded_int(
                values,
                MAX_CONCURRENT_CALLS_ENV,
                DEFAULT_MAX_CONCURRENT_CALLS,
                1,
                _MAX_CONCURRENT_CALLS,
            ),
        )


class _CallSlots:
    """Count local explanations in flight across the process; never wait for a slot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight = 0

    def try_acquire(self, capacity: int) -> bool:
        with self._lock:
            if self._in_flight >= capacity:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._in_flight -= 1


_CALL_SLOTS = _CallSlots()


def _seconds(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        seconds = float(raw)
    except ValueError as error:
        raise LLMConfigurationError(f"{name} must be a number of seconds, got {raw!r}") from error
    if not math.isfinite(seconds) or not 0 < seconds <= _MAX_OPERATION_SECONDS:
        raise LLMConfigurationError(f"{name} must be in (0, {_MAX_OPERATION_SECONDS:g}]")
    return seconds


def _byte_count(values: Mapping[str, str], name: str, default: int) -> int:
    return _bounded_int(values, name, default, _MIN_BYTES, _MAX_BYTES)


def _bounded_int(
    values: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        count = int(raw)
    except ValueError as error:
        raise LLMConfigurationError(f"{name} must be an integer, got {raw!r}") from error
    if not minimum <= count <= maximum:
        raise LLMConfigurationError(f"{name} must be in [{minimum}, {maximum}]")
    return count


def _permitted_name(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and len(value) <= _MAX_MODEL_LENGTH
        and _MODEL_NAME.fullmatch(value) is not None
        and not value.lower().rsplit(":", 1)[-1].endswith("cloud")
    )


class _BoundedTransport(httpx.AsyncBaseTransport):
    """Check limits before network I/O and before buffering response bytes."""

    def __init__(self, limits: LocalOllamaLimits) -> None:
        self._inner = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        self._limits = limits

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        max_response_bytes = self._limits.max_response_bytes
        if len(request.content) > self._limits.max_request_bytes:
            raise LLMProviderError("Local Ollama request exceeds byte limit")
        response = await self._inner.handle_async_request(request)
        try:
            if response.status_code != 200:
                raise LLMProviderError("Local Ollama request failed")
            encodings = response.headers.get_list("content-encoding")
            lengths = response.headers.get_list("content-length")
            if encodings and encodings != ["identity"]:
                raise LLMProviderError("Local Ollama encoded response rejected")
            if len(lengths) > 1 or (lengths and "transfer-encoding" in response.headers):
                raise LLMProviderError("Local Ollama ambiguous response framing")
            if lengths and (
                not lengths[0].isascii()
                or not lengths[0].isdigit()
                or int(lengths[0]) > max_response_bytes
            ):
                raise LLMProviderError("Local Ollama response exceeds byte limit")
            body = bytearray()
            if response.is_stream_consumed:
                chunks = [response.content]
                for chunk in chunks:
                    if len(chunk) > max_response_bytes:
                        raise LLMProviderError("Local Ollama response exceeds byte limit")
                    body.extend(chunk)
            else:
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > max_response_bytes:
                        raise LLMProviderError("Local Ollama response exceeds byte limit")
                    body.extend(chunk)
            if lengths and len(body) != int(lengths[0]):
                raise LLMProviderError("Local Ollama response length mismatch")
            return httpx.Response(200, headers=response.headers, content=bytes(body))
        finally:
            await response.aclose()


def _client(limits: LocalOllamaLimits, seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_BoundedTransport(limits),
        trust_env=False,
        follow_redirects=False,
        timeout=seconds,
        headers={"Accept-Encoding": "identity"},
    )


async def _models(client: httpx.AsyncClient) -> list[str]:
    response = await client.get(f"{LOCAL_OLLAMA_BASE_URL}/api/tags")
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        raise LLMProviderError("Local Ollama invalid model listing")
    models = body["models"]
    if len(models) > _MAX_MODELS:
        raise LLMProviderError("Local Ollama model listing exceeds limit")
    permitted: list[str] = []
    for entry in models:
        if not isinstance(entry, dict):
            raise LLMProviderError("Local Ollama invalid model entry")
        name = entry.get("name")
        alias = entry.get("model", name)
        if (
            not _permitted_name(name)
            or not _permitted_name(alias)
            or entry.get("remote_host")
            or entry.get("remote_model")
        ):
            continue
        if name not in permitted:
            permitted.append(name)
    return permitted


async def detect_local_ollama() -> list[str] | None:
    """Return installed local names, or None for unavailable/incompatible peers."""
    try:
        async with (
            asyncio.timeout(_PROBE_SECONDS),
            _client(LocalOllamaLimits(), _PROBE_SECONDS) as client,
        ):
            return await _models(client)
    except (httpx.HTTPError, TimeoutError, ValueError, RecursionError, LLMProviderError):
        return None


class LocalOllamaExplanationProvider:
    """Recheck local installation before sending evidence to the fixed peer."""

    def __init__(
        self,
        model: str,
        still_authorized: Callable[[], Awaitable[bool]],
        limits: LocalOllamaLimits | None = None,
    ) -> None:
        self._model = model
        self._still_authorized = still_authorized
        self._limits = limits

    async def _authorize_dispatch(self, _request: httpx.Request) -> None:
        # The catalog probe carries no evidence. Check committed consent at chat dispatch,
        # after probing and serialization, without holding a DB lock during network I/O.
        if not await self._still_authorized():
            raise LLMProviderError("Local Ollama consent changed before dispatch")

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        limits = self._limits if self._limits is not None else LocalOllamaLimits.from_environment()
        if not _CALL_SLOTS.try_acquire(limits.max_concurrent_calls):
            raise LLMProviderError("Local Ollama is busy with other explanations")
        try:
            return await self._generate(request, limits)
        finally:
            _CALL_SLOTS.release()

    async def _generate(
        self, request: ExplanationRequest, limits: LocalOllamaLimits
    ) -> GeneratedExplanation:
        try:
            async with (
                asyncio.timeout(limits.operation_seconds),
                _client(limits, limits.operation_seconds) as client,
            ):
                if not _permitted_name(self._model) or self._model not in await _models(client):
                    raise LLMProviderError("Local Ollama model is not installed locally")
                client.event_hooks["request"].append(self._authorize_dispatch)
                return await OllamaExplanationAdapter(
                    base_url=LOCAL_OLLAMA_BASE_URL,
                    model=self._model,
                    client=client,
                    context_tokens=limits.context_tokens,
                ).generate(request)
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError) as error:
            raise LLMProviderError("Local Ollama explanation unavailable") from error
