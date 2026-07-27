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

from pathlib import Path

import httpx
import pytest

from scan64.providers.llm import (
    HostedExplanationAdapter,
    LLMConfigurationError,
    OllamaExplanationAdapter,
    load_configured_llm_provider,
)


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
        '[llm]\nprovider = "ollama"\n'
        'model = "llama3"\n'
        'base_url = "http://localhost:11434"\n',
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
        '[llm]\nprovider = "ollama"\n'
        'model = "llama3"\n'
        'base_url = "http://localhost:11434"\n',
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
