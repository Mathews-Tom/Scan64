from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from scan64.explanations import assembly
from scan64.explanations.assembly import run_with_explanation_provider
from scan64.providers.llm import (
    ExplanationRequest,
    HostedExplanationAdapter,
    LLMConfigurationError,
    LLMExplanationProvider,
    LLMMessage,
    LLMProviderConfig,
    OllamaExplanationAdapter,
    create_llm_provider,
)
from scan64.providers.llm.adapters import LLMProviderError


def _request() -> ExplanationRequest:
    return ExplanationRequest(
        messages=(
            LLMMessage(role="system", content="Return only structured explanation claims."),
            LLMMessage(role="user", content="Explain the supplied verified evidence."),
        )
    )


def _explanation() -> dict[str, object]:
    return {
        "claims": [
            {
                "text": "The verified move attacks the target.",
                "evidence_ref": "ev_1",
                "line": ["e2e4"],
                "certainty": "observed",
                "disclosure_level": 1,
            }
        ],
    }


def _assert_strict_schema(value: object) -> None:
    if isinstance(value, dict):
        assert "default" not in value
        properties = value.get("properties")
        if isinstance(properties, dict):
            required = value.get("required")
            assert isinstance(required, list)
            assert set(required) == set(properties)
        for child in value.values():
            _assert_strict_schema(child)
    elif isinstance(value, list):
        for child in value:
            _assert_strict_schema(child)


@pytest.mark.asyncio
async def test_ollama_adapter_uses_json_schema_and_parses_response() -> None:
    observed_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(_explanation())}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OllamaExplanationAdapter(
            base_url="http://localhost:11434",
            model="local-model",
            client=client,
        )
        explanation = await adapter.generate(_request())

    assert observed_request["model"] == "local-model"
    assert observed_request["stream"] is False
    assert observed_request["think"] is False  # #429, also without context_tokens
    response_schema = observed_request["format"]
    assert isinstance(response_schema, dict)
    assert response_schema["additionalProperties"] is False
    assert explanation.claims[0].evidence_ref == "ev_1"


@pytest.mark.asyncio
async def test_hosted_adapter_uses_strict_function_calling_and_parses_response() -> None:
    observed_request: dict[str, object] = {}
    observed_authorization: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_authorization
        observed_authorization = request.headers.get("Authorization")
        observed_request.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "submit_grounded_explanation",
                                        "arguments": json.dumps(_explanation()),
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = HostedExplanationAdapter(
            base_url="https://llm.example.test",
            model="hosted-model",
            api_key="test-only-key",
            client=client,
        )
        explanation = await adapter.generate(_request())

    assert observed_authorization == "Bearer test-only-key"
    tools = observed_request["tools"]
    assert isinstance(tools, list)
    function = tools[0]["function"]
    assert function["strict"] is True
    _assert_strict_schema(function["parameters"])
    assert observed_request["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_grounded_explanation"},
    }
    assert explanation.claims[0].line == ("e2e4",)


def test_template_configuration_requires_no_http_client() -> None:
    assert create_llm_provider(LLMProviderConfig(provider="template")) is None


def test_enabled_provider_without_http_client_is_rejected() -> None:
    configuration = LLMProviderConfig(
        provider="ollama",
        model="local-model",
        base_url="http://localhost:11434",
    )

    with pytest.raises(LLMConfigurationError, match="HTTP client"):
        create_llm_provider(configuration)


# #414: the owned operator client uses llm.timeout_seconds, not httpx's 5 s.
# Lessons and the Analysis Board both reach the provider through
# run_with_explanation_provider, so these tests cover both paths.


def _operator_toml(tmp_path: Path, provider: str, base_url: str, extra: str = "") -> Path:
    path = tmp_path / "llm.toml"
    key = 'api_key_environment = "KEY"\n' if provider == "openai" else ""
    path.write_text(
        f'[llm]\nprovider = "{provider}"\nmodel = "m"\nbase_url = "{base_url}"\n{key}{extra}'
    )
    return path


async def _generate_or_fallback(provider: LLMExplanationProvider, _voice: object) -> str:
    try:
        await provider.generate(_request())
    except LLMProviderError:
        return "provider_error"
    return "narrator"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
@pytest.mark.parametrize(("extra", "seconds"), [("", 180.0), ("timeout_seconds = 45\n", 45.0)])
async def test_the_owned_operator_client_uses_the_configured_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, extra: str, seconds: float
) -> None:
    timeouts: list[httpx.Timeout] = []
    real_client = httpx.AsyncClient

    def recording_client(**kwargs: Any) -> httpx.AsyncClient:
        client = real_client(**kwargs)
        timeouts.append(client.timeout)
        return client

    monkeypatch.setattr(assembly.httpx, "AsyncClient", recording_client)
    path = _operator_toml(tmp_path, provider, "https://llm.example.com", extra)

    async def attempt(_provider: LLMExplanationProvider, _voice: object) -> str:
        return "narrator"

    result = await run_with_explanation_provider(
        "template",
        attempt,
        local_provider=None,
        environment={"SCAN64_LLM_CONFIG": str(path), "KEY": "secret"},
        client=None,
    )
    assert result == "narrator"
    assert timeouts == [httpx.Timeout(seconds)]


@asynccontextmanager
async def _slow_ollama(delay: float) -> AsyncIterator[str]:
    """A loopback Ollama that answers ``delay`` seconds after the request."""
    body = json.dumps({"message": {"content": json.dumps(_explanation())}}).encode()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        await reader.readexactly(length)
        await asyncio.sleep(delay)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        with contextlib.suppress(ConnectionError):
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield f"http://127.0.0.1:{port}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delay", "timeout", "expected"),
    [(0.3, 2.0, "narrator"), (1.0, 0.2, "provider_error")],
)
async def test_an_operator_answer_is_kept_within_the_timeout_and_dropped_after_it(
    tmp_path: Path, delay: float, timeout: float, expected: str
) -> None:
    async with _slow_ollama(delay) as base_url:
        path = _operator_toml(tmp_path, "ollama", base_url, f"timeout_seconds = {timeout}\n")
        result = await run_with_explanation_provider(
            "template",
            _generate_or_fallback,
            local_provider=None,
            environment={"SCAN64_LLM_CONFIG": str(path)},
            client=None,
        )
    assert result == expected
