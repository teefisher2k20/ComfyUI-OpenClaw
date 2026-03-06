"""
LLM Provider API Key Management.
R16/S11: Key lookup policy per provider with pluggable secret-provider chain.
"""

import logging
import os
from typing import Optional

from ..secret_providers import resolve_provider_secret
from .catalog import PROVIDER_CATALOG, get_provider_info

logger = logging.getLogger("ComfyUI-OpenClaw.services.providers.keys")

# Generic key names (new + legacy) for compatibility
GENERIC_KEY_NAMES = [
    "OPENCLAW_LLM_API_KEY",
    "MOLTBOT_LLM_API_KEY",
    "CLAWDBOT_LLM_API_KEY",
]


def get_api_key_for_provider(provider: str) -> Optional[str]:
    """
    Get API key for a specific provider.

    Precedence (S11/S25):
    1. Provider-specific env var (preferred: OPENCLAW_*; legacy: MOLTBOT_*)
    2. Generic env key (OPENCLAW_LLM_API_KEY, MOLTBOT_LLM_API_KEY, CLAWDBOT_LLM_API_KEY)
    3. Optional 1Password CLI provider (explicit opt-in + allowlisted command)
    4. Server secret store (encrypted server-side persistence)

    Returns None if no key found (acceptable for local providers).
    """
    key, _source = resolve_provider_secret(provider)
    return key


def requires_api_key(provider: str) -> bool:
    """Check if provider requires an API key."""
    provider_info = get_provider_info(provider)
    if not provider_info:
        return True  # Unknown provider, assume key required

    # Local providers don't require keys
    return provider_info.env_key_name is not None


def mask_api_key(key: str) -> str:
    """Mask API key for logging (never log full key)."""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


def get_all_configured_keys() -> dict:
    """
    Get a summary of configured keys (masked).
    Used for diagnostics, never returns actual key values.

    S25: Now includes server_store secrets.
    """
    result = {}

    # Check server secret store status (best-effort)
    store_status = {}
    try:
        from ..secret_store import get_secret_store

        store = get_secret_store()
        store_status = store.get_status()
    except Exception as e:
        logger.debug(f"S25: Failed to get secret store status (non-fatal): {e}")

    for provider_id, info in PROVIDER_CATALOG.items():
        if info.env_key_name:
            key = None
            provider_candidates = []
            if info.env_key_name.startswith("MOLTBOT_"):
                provider_candidates.append(
                    info.env_key_name.replace("MOLTBOT_", "OPENCLAW_", 1)
                )
            provider_candidates.append(info.env_key_name)

            source = None
            for env_name in provider_candidates:
                value = os.environ.get(env_name)
                if value:
                    key = value
                    source = "env"
                    break

            if key is None:
                for env_name in GENERIC_KEY_NAMES:
                    value = os.environ.get(env_name)
                    if value:
                        key = value
                        source = "env"
                        break

            if key is None and (
                provider_id in store_status or "generic" in store_status
            ):
                source = "server_store"

            if key is None and source is None:
                resolved, resolved_source = resolve_provider_secret(provider_id)
                if resolved:
                    key = resolved
                    source = resolved_source

            result[provider_id] = {
                "env_var": info.env_key_name,
                "configured": key is not None or source is not None,
                "masked": mask_api_key(key) if key and source == "env" else None,
                "source": source,
            }
        else:
            result[provider_id] = {
                "env_var": None,
                "configured": True,  # Local, always OK
                "masked": None,
                "source": "local",
            }

    # Add generic key status for diagnostics.
    generic_key = None
    generic_source = None
    for env_name in GENERIC_KEY_NAMES:
        value = os.environ.get(env_name)
        if value:
            generic_key = value
            generic_source = "env"
            break
    if generic_key is None and "generic" in store_status:
        generic_source = "server_store"
    if generic_key is None and generic_source is None:
        resolved, resolved_source = resolve_provider_secret("generic")
        if resolved:
            generic_key = resolved
            generic_source = resolved_source

    if generic_source is not None:
        result["generic"] = {
            "env_var": "OPENCLAW_LLM_API_KEY",
            "configured": True,
            "masked": mask_api_key(generic_key) if generic_source == "env" else None,
            "source": generic_source,
        }

    return result
