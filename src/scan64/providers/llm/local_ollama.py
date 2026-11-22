"""Private, bounded Ollama transport. No runtime destination configuration."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import TypeGuard

import httpx

from scan64.providers.llm.adapters import LLMProviderError, OllamaExplanationAdapter
from scan64.providers.llm.contracts import ExplanationRequest, GeneratedExplanation

LOCAL_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_OPERATION_SECONDS = 4.0
_MAX_REQUEST_BYTES = 128 * 1024
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_MODELS = 256
_MAX_MODEL_LENGTH = 128
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z")


def _permitted_name(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and len(value) <= _MAX_MODEL_LENGTH
        and _MODEL_NAME.fullmatch(value) is not None
        and not value.lower().rsplit(":", 1)[-1].endswith("cloud")
    )


class _BoundedTransport(httpx.AsyncBaseTransport):
    """Check limits before network I/O and before buffering response bytes."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport(trust_env=False, retries=0)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if len(request.content) > _MAX_REQUEST_BYTES:
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
                or int(lengths[0]) > _MAX_RESPONSE_BYTES
            ):
                raise LLMProviderError("Local Ollama response exceeds byte limit")
            body = bytearray()
            if response.is_stream_consumed:
                chunks = [response.content]
                for chunk in chunks:
                    if len(chunk) > _MAX_RESPONSE_BYTES:
                        raise LLMProviderError("Local Ollama response exceeds byte limit")
                    body.extend(chunk)
            else:
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise LLMProviderError("Local Ollama response exceeds byte limit")
                    body.extend(chunk)
            if lengths and len(body) != int(lengths[0]):
                raise LLMProviderError("Local Ollama response length mismatch")
            return httpx.Response(200, headers=response.headers, content=bytes(body))
        finally:
            await response.aclose()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_BoundedTransport(),
        trust_env=False,
        follow_redirects=False,
        timeout=_OPERATION_SECONDS,
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
        async with asyncio.timeout(_OPERATION_SECONDS), _client() as client:
            return await _models(client)
    except (httpx.HTTPError, TimeoutError, ValueError, RecursionError, LLMProviderError):
        return None


class LocalOllamaExplanationProvider:
    """Recheck local installation before sending evidence to the fixed peer."""

    def __init__(self, model: str, still_authorized: Callable[[], Awaitable[bool]]) -> None:
        self._model = model
        self._still_authorized = still_authorized

    async def _authorize_dispatch(self, _request: httpx.Request) -> None:
        # The catalog probe carries no evidence. Check committed consent at chat dispatch,
        # after probing and serialization, without holding a DB lock during network I/O.
        if not await self._still_authorized():
            raise LLMProviderError("Local Ollama consent changed before dispatch")

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        try:
            async with asyncio.timeout(_OPERATION_SECONDS), _client() as client:
                if not _permitted_name(self._model) or self._model not in await _models(client):
                    raise LLMProviderError("Local Ollama model is not installed locally")
                client.event_hooks["request"].append(self._authorize_dispatch)
                return await OllamaExplanationAdapter(
                    base_url=LOCAL_OLLAMA_BASE_URL,
                    model=self._model,
                    client=client,
                ).generate(request)
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError) as error:
            raise LLMProviderError("Local Ollama explanation unavailable") from error
