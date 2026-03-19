"""
R148 effective-config facade.

Provides one supported read surface for high-frequency LLM config consumers so
provider/model/base_url/api-key resolution does not drift across modules.
"""

from __future__ import annotations

from typing import Any

from .providers.catalog import DEFAULT_MODEL_BY_PROVIDER, DEFAULT_PROVIDER, get_provider_info
from .providers.keys import get_api_key_for_provider
from .runtime_config import get_effective_config


def get_effective_llm_config() -> tuple[dict[str, Any], dict[str, Any]]:
    return get_effective_config()


def get_effective_llm_provider() -> str:
    effective, _sources = get_effective_llm_config()
    return str(effective.get("provider") or DEFAULT_PROVIDER).lower()


def get_effective_llm_model(provider: str) -> str:
    effective, _sources = get_effective_llm_config()
    current_provider = str(effective.get("provider") or "").lower()
    configured_model = effective.get("model")
    if configured_model and str(provider).lower() == current_provider:
        return str(configured_model)
    return DEFAULT_MODEL_BY_PROVIDER.get(provider, "default")


def get_effective_llm_base_url(provider: str) -> str:
    effective, _sources = get_effective_llm_config()
    current_provider = str(effective.get("provider") or "").lower()
    configured_base_url = str(effective.get("base_url") or "").strip()
    if configured_base_url and str(provider).lower() == current_provider:
        return configured_base_url

    info = get_provider_info(provider)
    if info:
        return info.base_url

    raise ValueError(f"Unknown provider: {provider}")


def get_effective_llm_api_key(
    provider: str | None = None, tenant_id: str | None = None
) -> str | None:
    resolved_provider = str(provider or get_effective_llm_provider()).lower()
    return get_api_key_for_provider(resolved_provider, tenant_id=tenant_id)
