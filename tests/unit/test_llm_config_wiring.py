"""Unit coverage for the SCAN64_LLM_CONFIG-driven provider loader (M35 PR-3).

``load_configured_llm_provider`` is the single entry point that turns the
``SCAN64_LLM_CONFIG`` environment variable into an optional, deployment-
configured ``LLMExplanationProvider``. It is off by default: with the
variable unset, or pointed at an explicit ``provider = "template"``
configuration, it returns ``None`` and touches no client -- the caller must
render through the template floor. The default install therefore never
acquires a model dependency; enabling the LLM path is a deliberate,
operator-provided opt-in.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scan64.providers.llm import (
    HostedExplanationAdapter,
    LLMConfigurationError,
    OllamaExplanationAdapter,
    load_configured_llm_provider,
)
from scan64.providers.llm.adapters import LLMProviderError
from scan64.providers.llm.config import LLMProviderConfig
from scan64.providers.llm.contracts import ExplanationRequest, LLMMessage


def _write_toml(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "llm.toml"
    config_path.write_text(content)
    return config_path


def test_llm_config_absent_env_var_returns_none_without_reading_a_file() -> None:
    provider = load_configured_llm_provider(environment={})
    assert provider is None


def test_llm_config_explicit_template_provider_returns_none(tmp_path: Path) -> None:
    config_path = _write_toml(tmp_path, '[llm]\nprovider = "template"\n')
    provider = load_configured_llm_provider(environment={"SCAN64_LLM_CONFIG": str(config_path)})
    assert provider is None


@pytest.mark.asyncio
async def test_llm_config_selects_ollama_adapter_with_the_supplied_client(
    tmp_path: Path,
) -> None:
    config_path = _write_toml(
        tmp_path,
        '[llm]\nprovider = "ollama"\nmodel = "llama3"\nbase_url = "http://localhost:11434"\n',
    )
    async with httpx.AsyncClient() as client:
        provider = load_configured_llm_provider(
            client=client,
            environment={"SCAN64_LLM_CONFIG": str(config_path)},
        )
        assert isinstance(provider, OllamaExplanationAdapter)


@pytest.mark.asyncio
async def test_llm_config_selects_hosted_adapter_with_the_supplied_client(
    tmp_path: Path,
) -> None:
    config_path = _write_toml(
        tmp_path,
        '[llm]\nprovider = "openai"\n'
        'model = "gpt-4o-mini"\n'
        'base_url = "https://api.openai.com/v1"\n'
        'api_key_environment = "SCAN64_OPENAI_KEY"\n',
    )
    async with httpx.AsyncClient() as client:
        provider = load_configured_llm_provider(
            client=client,
            environment={
                "SCAN64_LLM_CONFIG": str(config_path),
                "SCAN64_OPENAI_KEY": "test-key",
            },
        )
        assert isinstance(provider, HostedExplanationAdapter)


@pytest.mark.asyncio
async def test_llm_config_enabled_without_a_client_is_rejected(tmp_path: Path) -> None:
    config_path = _write_toml(
        tmp_path,
        '[llm]\nprovider = "ollama"\nmodel = "llama3"\nbase_url = "http://localhost:11434"\n',
    )
    with pytest.raises(LLMConfigurationError, match="HTTP client is required"):
        load_configured_llm_provider(environment={"SCAN64_LLM_CONFIG": str(config_path)})


@pytest.mark.asyncio
async def test_llm_config_rejects_a_relative_config_path() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMConfigurationError, match="must be absolute"):
            load_configured_llm_provider(
                client=client,
                environment={"SCAN64_LLM_CONFIG": "relative/llm.toml"},
            )


@pytest.mark.asyncio
async def test_llm_config_rejects_an_unsupported_provider_value(tmp_path: Path) -> None:
    config_path = _write_toml(tmp_path, '[llm]\nprovider = "anthropic"\n')
    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMConfigurationError, match="template, ollama, or openai"):
            load_configured_llm_provider(
                client=client,
                environment={"SCAN64_LLM_CONFIG": str(config_path)},
            )


def _ollama_toml(tmp_path: Path, extra: str = "") -> Path:
    return _write_toml(
        tmp_path,
        '[llm]\nprovider = "ollama"\nmodel = "llama3"\n'
        f'base_url = "http://localhost:11434"\n{extra}',
    )


def _ollama_reply(prompt_tokens: int) -> httpx.Response:
    claim = {"text": "t", "evidence_ref": "ev_1", "disclosure_level": 1}
    return httpx.Response(
        200,
        json={
            "prompt_eval_count": prompt_tokens,
            "message": {"content": json.dumps({"claims": [claim]})},
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("extra", "context"), [("", 12288), ("context_tokens = 16384\n", 16384)])
async def test_llm_config_ollama_sends_num_ctx_and_rejects_a_filled_context(
    tmp_path: Path, extra: str, context: int
) -> None:
    """#388: without num_ctx the server default (4096) silently truncates the prompt."""
    sent: list[object] = []
    prompt_tokens = [context - 1, context]

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content)["options"])
        return _ollama_reply(prompt_tokens.pop(0))

    environment = {"SCAN64_LLM_CONFIG": str(_ollama_toml(tmp_path, extra))}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = load_configured_llm_provider(client=client, environment=environment)
        assert isinstance(provider, OllamaExplanationAdapter)
        await provider.generate(_request())
        with pytest.raises(LLMProviderError, match="truncated"):
            await provider.generate(_request())
    assert sent == [{"num_ctx": context}, {"num_ctx": context}]


@pytest.mark.parametrize("value", ["2047", "131073", "true", '"12288"', "8192.0"])
def test_llm_config_rejects_an_invalid_context_tokens(tmp_path: Path, value: str) -> None:
    path = _ollama_toml(tmp_path, f"context_tokens = {value}\n")
    with pytest.raises(LLMConfigurationError, match="context_tokens"):
        LLMProviderConfig.from_toml(path)


def test_llm_config_rejects_context_tokens_for_a_hosted_provider(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        '[llm]\nprovider = "openai"\nmodel = "m"\nbase_url = "https://api.example.com"\n'
        'api_key_environment = "KEY"\ncontext_tokens = 12288\n',
    )
    with pytest.raises(LLMConfigurationError, match="context_tokens"):
        LLMProviderConfig.from_toml(path)


def _request() -> ExplanationRequest:
    return ExplanationRequest(messages=(LLMMessage(role="user", content="evidence"),))
