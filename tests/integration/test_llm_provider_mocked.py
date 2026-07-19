from __future__ import annotations

import json

import httpx
import pytest

from scan64.providers.llm import (
    ExplanationRequest,
    HostedExplanationAdapter,
    LLMConfigurationError,
    LLMMessage,
    LLMProviderConfig,
    OllamaExplanationAdapter,
    create_llm_provider,
)


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
