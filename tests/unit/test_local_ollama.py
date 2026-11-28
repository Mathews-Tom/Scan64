from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from sys import getrecursionlimit

import httpx
import pytest

from scan64.providers.llm import local_ollama as local
from scan64.providers.llm.adapters import LLMProviderError
from scan64.providers.llm.config import MAX_CONCURRENT_CALLS_ENV, LLMConfigurationError
from scan64.providers.llm.contracts import ExplanationRequest, LLMMessage


@pytest.fixture
def peer(monkeypatch: pytest.MonkeyPatch) -> Callable:
    def install(handler: Callable) -> None:
        def transport(**kwargs: object) -> httpx.MockTransport:
            assert kwargs == {"trust_env": False, "retries": 0}
            return httpx.MockTransport(handler)

        monkeypatch.setattr(local.httpx, "AsyncHTTPTransport", transport)

    return install


def request() -> ExplanationRequest:
    return ExplanationRequest(messages=(LLMMessage(role="user", content="private evidence"),))


async def authorized() -> bool:
    return True


def tags(*entries: object) -> httpx.Response:
    return httpx.Response(200, json={"models": list(entries)})


@pytest.mark.asyncio
async def test_probe_filters_remote_models_without_evidence(
    peer: Callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://invalid.example:1")
    monkeypatch.setenv("OLLAMA_HOST", "http://invalid.example:1")

    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "http://127.0.0.1:11434/api/tags"
        assert req.method == "GET" and not req.content
        assert "authorization" not in req.headers
        assert req.headers["accept-encoding"] == "identity"
        return tags(
            {"name": "gemma3:latest", "model": "gemma3:latest"},
            {"name": "cloud-model:cloud"},
            {"name": "remote:latest", "remote_host": "https://ollama.com"},
            {"name": "remote2:latest", "remote_model": "gpt:latest"},
            {"name": "alias:latest", "model": "gpt:cloud"},
        )

    peer(handler)
    assert await local.detect_local_ollama() == ["gemma3:latest"]


@pytest.mark.asyncio
async def test_generation_fresh_probe_then_only_local_chat(peer: Callable) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        assert "authorization" not in req.headers
        if req.url.path == "/api/tags":
            assert not req.content
            return tags({"name": "gemma3:latest"})
        body = json.loads(req.content)
        assert body["model"] == "gemma3:latest"
        assert body["stream"] is False
        assert body["messages"] == [{"role": "user", "content": "private evidence"}]
        assert body["format"]["additionalProperties"] is False
        assert body["options"] == {"num_ctx": local.DEFAULT_CONTEXT_TOKENS}
        assert body["think"] is False
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "claims": [
                                {
                                    "text": "Verified move.",
                                    "evidence_ref": "ev_1",
                                    "line": ["e2e4"],
                                    "certainty": "observed",
                                    "disclosure_level": 1,
                                }
                            ]
                        }
                    )
                }
            },
        )

    peer(handler)
    result = await local.LocalOllamaExplanationProvider("gemma3:latest", authorized).generate(
        request()
    )
    assert result.claims[0].evidence_ref == "ev_1"
    assert calls == ["/api/tags", "/api/chat"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        None,
        {"name": "chosen:cloud"},
        {"name": "chosen", "remote_model": "remote"},
        {"name": "other"},
    ],
)
async def test_uninstalled_or_remote_never_sends_evidence(peer: Callable, entry: object) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return tags(*([] if entry is None else [entry]))

    peer(handler)
    with pytest.raises(LLMProviderError):
        await local.LocalOllamaExplanationProvider("chosen", authorized).generate(request())
    assert calls == ["/api/tags"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://example.com"}),
        httpx.Response(500),
        httpx.Response(200, content=b"invalid"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"models": [{} for _ in range(257)]}),
        httpx.Response(200, content=b"x" * (256 * 1024 + 1)),
        httpx.Response(
            200, stream=httpx.ByteStream(b"compressed"), headers={"content-encoding": "gzip"}
        ),
        httpx.Response(
            200, content=b"{}", headers=[("content-length", "2"), ("content-length", "2")]
        ),
        httpx.Response(
            200, content=b"{}", headers={"content-length": "2", "transfer-encoding": "chunked"}
        ),
    ],
)
async def test_invalid_transport_is_unavailable(peer: Callable, response: httpx.Response) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return response

    peer(handler)
    assert await local.detect_local_ollama() is None
    assert calls == ["/api/tags"]


@pytest.mark.asyncio
async def test_empty_compatible_listing(peer: Callable) -> None:
    peer(lambda req: tags())
    assert await local.detect_local_ollama() == []


@pytest.mark.asyncio
async def test_connection_failure(peer: Callable) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    peer(handler)
    assert await local.detect_local_ollama() is None
    with pytest.raises(LLMProviderError):
        await local.LocalOllamaExplanationProvider("local", authorized).generate(request())


@pytest.mark.asyncio
async def test_absolute_deadline(peer: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "_PROBE_SECONDS", 0.01)
    monkeypatch.setenv(local.TIMEOUT_ENV, "0.01")

    async def handler(req: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return tags()

    peer(handler)
    assert await local.detect_local_ollama() is None
    with pytest.raises(LLMProviderError):
        await local.LocalOllamaExplanationProvider("local", authorized).generate(request())


@pytest.mark.asyncio
async def test_request_byte_limit_prevents_chat(peer: Callable) -> None:
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        return tags({"name": "local"})

    peer(handler)
    oversized = ExplanationRequest(
        messages=(LLMMessage(role="user", content="x" * local.DEFAULT_MAX_REQUEST_BYTES),)
    )
    with pytest.raises(LLMProviderError):
        await local.LocalOllamaExplanationProvider("local", authorized).generate(oversized)
    assert paths == ["/api/tags"]


@pytest.mark.asyncio
async def test_configured_request_byte_limit_prevents_chat(
    peer: Callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        return tags({"name": "local"})

    peer(handler)
    monkeypatch.setenv(local.MAX_REQUEST_BYTES_ENV, "2048")
    small = ExplanationRequest(messages=(LLMMessage(role="user", content="x" * 4096),))
    with pytest.raises(LLMProviderError):
        await local.LocalOllamaExplanationProvider("local", authorized).generate(small)
    assert paths == ["/api/tags"]


def test_limits_default_and_parse_from_the_environment() -> None:
    assert local.LocalOllamaLimits.from_environment({}) == local.LocalOllamaLimits(
        operation_seconds=local.DEFAULT_OPERATION_SECONDS,
        max_request_bytes=local.DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes=local.DEFAULT_MAX_RESPONSE_BYTES,
    )
    assert local.LocalOllamaLimits.from_environment(
        {
            local.TIMEOUT_ENV: "12.5",
            local.MAX_REQUEST_BYTES_ENV: "65536",
            local.MAX_RESPONSE_BYTES_ENV: "131072",
        }
    ) == local.LocalOllamaLimits(12.5, 65536, 131072, local.DEFAULT_CONTEXT_TOKENS)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SCAN64_LOCAL_LLM_TIMEOUT_SECONDS", "0"),
        ("SCAN64_LOCAL_LLM_TIMEOUT_SECONDS", "nan"),
        ("SCAN64_LOCAL_LLM_TIMEOUT_SECONDS", "601"),
        ("SCAN64_LOCAL_LLM_TIMEOUT_SECONDS", "soon"),
        ("SCAN64_LOCAL_LLM_MAX_REQUEST_BYTES", "12"),
        ("SCAN64_LOCAL_LLM_MAX_RESPONSE_BYTES", "1.5"),
        ("SCAN64_LOCAL_LLM_MAX_RESPONSE_BYTES", str(17 * 1024 * 1024)),
        ("SCAN64_LOCAL_LLM_CONTEXT_TOKENS", "1024"),
    ],
)
def test_invalid_limits_fail_loudly(name: str, value: str) -> None:
    with pytest.raises(LLMConfigurationError, match=name):
        local.LocalOllamaLimits.from_environment({name: value})


@pytest.mark.asyncio
async def test_invalid_limit_is_not_swallowed_as_unavailable(
    peer: Callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    peer(lambda req: tags({"name": "local"}))
    monkeypatch.setenv(local.TIMEOUT_ENV, "-1")
    with pytest.raises(LLMConfigurationError):
        await local.LocalOllamaExplanationProvider("local", authorized).generate(request())


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["catalog", "reply", "claim"])
async def test_deeply_nested_daemon_json_is_unavailable(peer: Callable, stage: str) -> None:
    depth = getrecursionlimit() + 100
    nested = "[" * depth + "0" + "]" * depth

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            if stage == "catalog":
                return httpx.Response(200, content=nested)
            return tags({"name": "local"})
        if stage == "reply":
            return httpx.Response(200, content=nested)
        return httpx.Response(200, json={"message": {"content": nested}})

    peer(handler)
    assert await local.detect_local_ollama() == (None if stage == "catalog" else ["local"])
    with pytest.raises(LLMProviderError):
        await local.LocalOllamaExplanationProvider("local", authorized).generate(request())


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * (128 * 1024)
        yield b"x" * (128 * 1024 + 1)
        raise AssertionError("must stop reading at the byte limit")


@pytest.mark.asyncio
async def test_stream_is_bounded_before_buffering(peer: Callable) -> None:
    peer(lambda req: httpx.Response(200, stream=Chunks()))
    assert await local.detect_local_ollama() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("context", "prompt_tokens"), [(None, 4096), ("8192", 8192)])
async def test_prompt_that_fills_the_context_is_rejected(
    peer: Callable, monkeypatch: pytest.MonkeyPatch, context: str | None, prompt_tokens: int
) -> None:
    if context is not None:
        monkeypatch.setenv(local.CONTEXT_TOKENS_ENV, context)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return tags({"name": "local"})
        assert json.loads(req.content)["options"] == {"num_ctx": prompt_tokens}
        claim = {"text": "t", "evidence_ref": "ev_1", "disclosure_level": 1}
        return httpx.Response(
            200,
            json={
                "prompt_eval_count": prompt_tokens,
                "message": {"content": json.dumps({"claims": [claim]})},
            },
        )

    peer(handler)
    with pytest.raises(LLMProviderError, match="truncated"):
        await local.LocalOllamaExplanationProvider("local", authorized).generate(request())


def test_concurrent_call_limit_parses_from_the_environment() -> None:
    assert local.LocalOllamaLimits.from_environment({}).max_concurrent_calls == 2
    limits = local.LocalOllamaLimits.from_environment({MAX_CONCURRENT_CALLS_ENV: "1"})
    assert limits.max_concurrent_calls == 1
    for value in ("0", "9", "two"):
        with pytest.raises(LLMConfigurationError):
            local.LocalOllamaLimits.from_environment({MAX_CONCURRENT_CALLS_ENV: value})


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [1, 2])
async def test_call_over_the_process_limit_fails_at_once(
    peer: Callable, monkeypatch: pytest.MonkeyPatch, capacity: int
) -> None:
    monkeypatch.setenv(MAX_CONCURRENT_CALLS_ENV, str(capacity))
    entered = asyncio.Semaphore(0)
    release = asyncio.Event()
    chats = 0

    async def handler(req: httpx.Request) -> httpx.Response:
        nonlocal chats
        if req.url.path == "/api/tags":
            return tags({"name": "local"})
        chats += 1
        entered.release()
        await release.wait()
        claim = {"text": "t", "evidence_ref": "ev_1", "disclosure_level": 1}
        return httpx.Response(200, json={"message": {"content": json.dumps({"claims": [claim]})}})

    peer(handler)

    def provider() -> local.LocalOllamaExplanationProvider:
        return local.LocalOllamaExplanationProvider("local", authorized)

    holders = [asyncio.create_task(provider().generate(request())) for _ in range(capacity)]
    for _ in range(capacity):
        await entered.acquire()
    # Every slot is held at the chat: the next call fails without reaching the peer.
    with pytest.raises(LLMProviderError, match="busy"):
        await provider().generate(request())
    assert chats == capacity
    release.set()
    for generated in await asyncio.gather(*holders):
        assert generated.claims[0].text == "t"
    # The slots are free again once the calls end.
    assert (await provider().generate(request())).claims[0].text == "t"
