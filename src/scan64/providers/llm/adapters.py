from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, cast

import httpx
from pydantic import ValidationError

from scan64.providers.llm.contracts import ExplanationRequest, GeneratedExplanation


class LLMExplanationProvider(Protocol):
    """Provider boundary for schema-constrained explanation generation."""

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation: ...


class LLMProviderError(RuntimeError):
    """Raised when an LLM provider cannot produce a valid structured response."""


def _schema() -> dict[str, object]:
    return cast(dict[str, object], GeneratedExplanation.model_json_schema())


def _strict_schema() -> dict[str, object]:
    schema = _schema()
    _require_all_properties(schema)
    return schema


def _require_all_properties(value: object) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict) and all(isinstance(key, str) for key in properties):
            value["required"] = list(properties)
        value.pop("default", None)
        for child in value.values():
            _require_all_properties(child)
    elif isinstance(value, list):
        for child in value:
            _require_all_properties(child)


def _messages(request: ExplanationRequest) -> list[dict[str, str]]:
    return [message.model_dump() for message in request.messages]


def _response_object(response: httpx.Response, provider: str) -> Mapping[str, object]:
    try:
        body = cast(object, response.json())
    except json.JSONDecodeError as error:
        raise LLMProviderError(f"{provider} returned invalid JSON") from error
    if not isinstance(body, dict) or not all(isinstance(key, str) for key in body):
        raise LLMProviderError(f"{provider} returned a non-object response")
    return cast(Mapping[str, object], body)


def _required_object(value: object, field: str, provider: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise LLMProviderError(f"{provider} response lacks object field {field!r}")
    return cast(Mapping[str, object], value)


def _required_list(value: object, field: str, provider: str) -> list[object]:
    if not isinstance(value, list):
        raise LLMProviderError(f"{provider} response lacks list field {field!r}")
    return list(value)


def _parse_generated_explanation(raw: str, provider: str) -> GeneratedExplanation:
    try:
        return GeneratedExplanation.model_validate_json(raw)
    except ValidationError as error:
        raise LLMProviderError(f"{provider} returned an invalid explanation schema") from error


class OllamaExplanationAdapter:
    """Ollama-compatible adapter using its JSON-schema response format."""

    def __init__(self, *, base_url: str, model: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        try:
            response = await self._client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": _messages(request),
                    "stream": False,
                    "format": _schema(),
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise LLMProviderError("Ollama explanation request failed") from error

        body = _response_object(response, "Ollama")
        message = _required_object(body.get("message"), "message", "Ollama")
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMProviderError("Ollama response lacks string message.content")
        return _parse_generated_explanation(content, "Ollama")


class HostedExplanationAdapter:
    """OpenAI-compatible adapter using strict function-calling output."""

    _TOOL_NAME = "submit_grounded_explanation"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        client: httpx.AsyncClient,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = client

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": _messages(request),
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": self._TOOL_NAME,
                                "description": "Return the grounded explanation claim objects.",
                                "strict": True,
                                "parameters": _strict_schema(),
                            },
                        }
                    ],
                    "tool_choice": {
                        "type": "function",
                        "function": {"name": self._TOOL_NAME},
                    },
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise LLMProviderError("Hosted explanation request failed") from error

        body = _response_object(response, "Hosted provider")
        choices = _required_list(body.get("choices"), "choices", "Hosted provider")
        if not choices:
            raise LLMProviderError("Hosted provider returned no choices")
        choice = _required_object(choices[0], "choices[0]", "Hosted provider")
        message = _required_object(choice.get("message"), "message", "Hosted provider")
        tool_calls = _required_list(message.get("tool_calls"), "tool_calls", "Hosted provider")
        if not tool_calls:
            raise LLMProviderError("Hosted provider returned no function call")
        tool_call = _required_object(tool_calls[0], "tool_calls[0]", "Hosted provider")
        function = _required_object(tool_call.get("function"), "function", "Hosted provider")
        name = function.get("name")
        arguments = function.get("arguments")
        if name != self._TOOL_NAME or not isinstance(arguments, str):
            raise LLMProviderError("Hosted provider returned an unexpected function call")
        return _parse_generated_explanation(arguments, "Hosted provider")
