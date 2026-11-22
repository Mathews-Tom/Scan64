from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from sys import getrecursionlimit

import httpx
import pytest

from scan64.providers.llm import local_ollama as local
from scan64.providers.llm.adapters import LLMProviderError
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
    monkeypatch.setattr(local, "_OPERATION_SECONDS", 0.01)

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
    oversized = ExplanationRequest(messages=(LLMMessage(role="user", content="x" * (128 * 1024)),))
    with pytest.raises(LLMProviderError):
        await local.LocalOllamaExplanationProvider("local", authorized).generate(oversized)
    assert paths == ["/api/tags"]


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
