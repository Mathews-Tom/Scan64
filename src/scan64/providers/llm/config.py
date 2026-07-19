from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from scan64.providers.llm.adapters import (
    HostedExplanationAdapter,
    LLMExplanationProvider,
    OllamaExplanationAdapter,
)


class LLMConfigurationError(RuntimeError):
    """Raised when deployment configuration cannot safely select an LLM provider."""


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    model: str | None = None
    base_url: str | None = None
    api_key_environment: str | None = None

    @classmethod
    def from_toml(cls, path: Path) -> LLMProviderConfig:
        if not path.is_absolute():
            raise LLMConfigurationError("LLM configuration path must be absolute")
        try:
            with path.open("rb") as config_file:
                raw_config = tomllib.load(config_file)
        except FileNotFoundError as error:
            raise LLMConfigurationError(f"LLM configuration does not exist: {path}") from error
        except tomllib.TOMLDecodeError as error:
            raise LLMConfigurationError(f"Invalid LLM configuration: {path}") from error

        raw_llm = raw_config.get("llm")
        if not isinstance(raw_llm, dict):
            raise LLMConfigurationError("LLM configuration requires an [llm] table")
        return cls._from_mapping(raw_llm)

    @classmethod
    def _from_mapping(cls, raw_llm: Mapping[object, object]) -> LLMProviderConfig:
        provider = raw_llm.get("provider")
        if not isinstance(provider, str):
            raise LLMConfigurationError("llm.provider must be template, ollama, or openai")
        if provider == "template":
            return cls(provider=provider)
        if provider not in {"ollama", "openai"}:
            raise LLMConfigurationError("llm.provider must be template, ollama, or openai")

        model = raw_llm.get("model")
        base_url = raw_llm.get("base_url")
        if not isinstance(model, str) or not model:
            raise LLMConfigurationError("llm.model must be a non-empty string")
        if not isinstance(base_url, str) or not base_url:
            raise LLMConfigurationError("llm.base_url must be a non-empty string")

        api_key_environment = raw_llm.get("api_key_environment")
        if provider == "openai":
            if not isinstance(api_key_environment, str) or not api_key_environment:
                raise LLMConfigurationError(
                    "OpenAI-compatible configuration requires llm.api_key_environment"
                )
            return cls(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key_environment=api_key_environment,
            )
        return cls(provider=provider, model=model, base_url=base_url)


def create_llm_provider(
    config: LLMProviderConfig,
    *,
    client: httpx.AsyncClient | None = None,
    environment: Mapping[str, str] | None = None,
) -> LLMExplanationProvider | None:
    """Select an additive LLM provider; template mode deliberately returns no LLM."""

    if config.provider == "template":
        return None
    if client is None:
        raise LLMConfigurationError("An HTTP client is required for an LLM provider")
    if config.provider == "ollama":
        return OllamaExplanationAdapter(
            base_url=_validated_base_url(config.base_url, allow_loopback_http=True),
            model=_required_value(config.model, "llm.model"),
            client=client,
        )
    if config.provider == "openai":
        environment_values = os.environ if environment is None else environment
        api_key_name = _required_value(config.api_key_environment, "llm.api_key_environment")
        api_key = environment_values.get(api_key_name)
        if not api_key:
            raise LLMConfigurationError(f"Environment variable {api_key_name} is required")
        return HostedExplanationAdapter(
            base_url=_validated_base_url(config.base_url, allow_loopback_http=False),
            model=_required_value(config.model, "llm.model"),
            api_key=api_key,
            client=client,
        )
    raise LLMConfigurationError("Unsupported LLM provider")


def _required_value(value: str | None, field: str) -> str:
    if value is None:
        raise LLMConfigurationError(f"{field} is required")
    return value


def _validated_base_url(base_url: str | None, *, allow_loopback_http: bool) -> str:
    value = _required_value(base_url, "llm.base_url")
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return value.rstrip("/")
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}
    if allow_loopback_http and parsed.scheme == "http" and parsed.hostname in loopback_hosts:
        return value.rstrip("/")
    raise LLMConfigurationError("llm.base_url must use HTTPS or loopback HTTP for Ollama")
