"""
LLM Provider Catalog.
R16: Default base URLs and provider metadata.
R73: Provider drift governance — alias/deprecation metadata and resolution trace.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse


class ProviderType(Enum):
    """Provider API type."""

    OPENAI_COMPAT = "openai_compat"
    ANTHROPIC = "anthropic"


@dataclass
class ProviderInfo:
    """Provider metadata."""

    name: str
    base_url: str
    api_type: ProviderType
    supports_vision: bool = False
    env_key_name: Optional[str] = None  # e.g., "MOLTBOT_OPENAI_API_KEY"


# Default provider catalog
PROVIDER_CATALOG: Dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name="MOLTBOT_OPENAI_API_KEY",
    ),
    "anthropic": ProviderInfo(
        name="Anthropic",
        base_url="https://api.anthropic.com",
        api_type=ProviderType.ANTHROPIC,
        supports_vision=True,
        env_key_name="MOLTBOT_ANTHROPIC_API_KEY",
    ),
    "openrouter": ProviderInfo(
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name="MOLTBOT_OPENROUTER_API_KEY",
    ),
    "gemini": ProviderInfo(
        name="Gemini (OpenAI-compat)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name="MOLTBOT_GEMINI_API_KEY",
    ),
    "groq": ProviderInfo(
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=False,
        env_key_name="MOLTBOT_GROQ_API_KEY",
    ),
    "deepseek": ProviderInfo(
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=False,
        env_key_name="MOLTBOT_DEEPSEEK_API_KEY",
    ),
    "xai": ProviderInfo(
        name="xAI",
        base_url="https://api.x.ai/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=False,
        env_key_name="MOLTBOT_XAI_API_KEY",
    ),
    "ollama": ProviderInfo(
        name="Ollama (Local)",
        base_url="http://127.0.0.1:11434/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name=None,  # Local, no key needed
    ),
    "lmstudio": ProviderInfo(
        name="LM Studio (Local)",
        base_url="http://localhost:1234/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name=None,  # Local, no key needed
    ),
    "antigravity_proxy": ProviderInfo(
        name="Antigravity Claude Proxy (Local)",
        base_url="http://127.0.0.1:8080",
        api_type=ProviderType.ANTHROPIC,
        supports_vision=True,
        env_key_name=None,  # R35: Proxy runs without auth (loopback-only default)
    ),
    "custom": ProviderInfo(
        name="Custom",
        base_url="",  # User must provide
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=False,
        env_key_name="MOLTBOT_CUSTOM_API_KEY",
    ),
}

# Default provider and model
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL_BY_PROVIDER = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
    "openrouter": "anthropic/claude-sonnet-4-20250514",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
    "xai": "grok-3",
    "ollama": "llama3.2",
    "lmstudio": "default",
    "antigravity_proxy": "claude-sonnet-4-20250514",  # R35: Conservative default
    "custom": "default",
}

# R24: Alias Tables
PROVIDER_ALIASES: Dict[str, str] = {
    "chatgpt": "openai",
    "claude": "anthropic",
    "local": "lmstudio",  # Ambiguous, but map to one
    # Common typos/variations
    "open-ai": "openai",
    "antheropic": "anthropic",
    # R35: Antigravity proxy alias
    "antigravity-proxy": "antigravity_proxy",
}

MODEL_ALIASES: Dict[str, str] = {
    # OpenAI
    "gpt4": "gpt-4",
    "gpt35": "gpt-3.5-turbo",
    "gpt-3.5": "gpt-3.5-turbo",
    # Anthropic
    "claude3": "claude-3-opus-20240229",
    "opus": "claude-3-opus-20240229",
    "sonnet": "claude-3-sonnet-20240229",
    "haiku": "claude-3-haiku-20240307",
    # Gemini
    "gemini": "gemini-pro",
    "gemini15": "gemini-1.5-pro",
    # Meta
    "llama3": "llama3.1-70b",
}


# R73: Deprecated aliases — maps obsolete names to canonical names + deprecation message.
@dataclass
class DeprecationEntry:
    """Tracks a deprecated alias or model ID."""

    canonical: str
    message: str
    since_version: str = ""


DEPRECATED_PROVIDER_ALIASES: Dict[str, DeprecationEntry] = {
    "bard": DeprecationEntry(
        canonical="gemini",
        message="'bard' has been renamed to 'gemini'. Please update your config.",
        since_version="0.9.0",
    ),
}

DEPRECATED_MODEL_ALIASES: Dict[str, DeprecationEntry] = {
    "gpt-3.5-turbo": DeprecationEntry(
        canonical="gpt-4o-mini",
        message="'gpt-3.5-turbo' is deprecated; consider 'gpt-4o-mini' as a cost-effective replacement.",
        since_version="0.8.0",
    ),
    "claude-3-sonnet-20240229": DeprecationEntry(
        canonical="claude-sonnet-4-20250514",
        message="'claude-3-sonnet-20240229' is outdated; consider 'claude-sonnet-4-20250514'.",
        since_version="0.9.0",
    ),
    "gemini-pro": DeprecationEntry(
        canonical="gemini-2.0-flash",
        message="'gemini-pro' is deprecated; consider 'gemini-2.0-flash'.",
        since_version="0.9.0",
    ),
}


def resolve_provider_with_trace(provider: str) -> Tuple[str, List[str]]:
    """
    R73: Resolve provider ID through alias + deprecation chain.

    Returns:
        (final_provider_id, list_of_diagnostic_messages)
    """
    trace: List[str] = []
    original = provider
    p = provider.lower().strip()

    # Step 1: Check deprecated aliases (logs warning)
    dep = DEPRECATED_PROVIDER_ALIASES.get(p)
    if dep:
        trace.append(f"DEPRECATED: '{p}' -> '{dep.canonical}' ({dep.message})")
        p = dep.canonical

    # Step 2: Check regular aliases
    if p in PROVIDER_ALIASES:
        canonical = PROVIDER_ALIASES[p]
        trace.append(f"ALIAS: '{p}' -> '{canonical}'")
        p = canonical

    if p != original.lower().strip():
        trace.insert(0, f"INPUT: '{original}'")
        trace.append(f"FINAL: '{p}'")
    else:
        trace.append(f"RESOLVED: '{p}' (no transformation)")

    return p, trace


def resolve_model_with_trace(model: str) -> Tuple[str, List[str]]:
    """
    R73: Resolve model ID through alias + deprecation chain.

    Returns:
        (final_model_id, list_of_diagnostic_messages)
    """
    trace: List[str] = []
    original = model
    m = model.strip()
    m_lower = m.lower()

    # Step 1: Check deprecated models
    dep = DEPRECATED_MODEL_ALIASES.get(m)
    if not dep:
        dep = DEPRECATED_MODEL_ALIASES.get(m_lower)
    if dep:
        trace.append(f"DEPRECATED: '{m}' -> '{dep.canonical}' ({dep.message})")
        # Note: We do NOT auto-replace deprecated models — only warn.
        # User should explicitly update.

    # Step 2: Check regular aliases
    alias = MODEL_ALIASES.get(m_lower)
    if alias:
        trace.append(f"ALIAS: '{m_lower}' -> '{alias}'")
        m = alias
    else:
        # Preserve original casing if no alias match
        pass

    if m != original:
        trace.insert(0, f"INPUT: '{original}'")
        trace.append(f"FINAL: '{m}'")
    else:
        trace.append(f"RESOLVED: '{m}' (no transformation)")

    return m, trace


def get_provider_governance_info() -> Dict[str, dict]:
    """
    R73: Return governance metadata for all providers.
    Used for diagnostics and frontend display.
    """
    info = {}
    for pid, pinfo in PROVIDER_CATALOG.items():
        entry: dict = {
            "name": pinfo.name,
            "default_model": DEFAULT_MODEL_BY_PROVIDER.get(pid),
            "api_type": pinfo.api_type.value,
            "requires_key": pinfo.env_key_name is not None,
        }
        # Check if any deprecated alias points here
        dep_aliases = [
            k for k, v in DEPRECATED_PROVIDER_ALIASES.items() if v.canonical == pid
        ]
        if dep_aliases:
            entry["deprecated_aliases"] = dep_aliases

        # Check regular aliases
        aliases = [k for k, v in PROVIDER_ALIASES.items() if v == pid]
        if aliases:
            entry["aliases"] = aliases

        info[pid] = entry
    return info


def normalize_provider_id(provider: str) -> str:
    """Normalize provider ID (resolve deprecated + regular aliases)."""
    p = provider.lower().strip()
    # R73: Check deprecated aliases first
    dep = DEPRECATED_PROVIDER_ALIASES.get(p)
    if dep:
        p = dep.canonical
    return PROVIDER_ALIASES.get(p, p)


def normalize_provider_base_url(provider: str, base_url: str) -> str:
    """
    Normalize provider-specific base URL compatibility seams.

    Currently used to keep Ollama's OpenAI-compatible endpoint path aligned to
    `/v1` even when older persisted configs still store the historical root URL.
    """
    value = str(base_url or "").strip()
    if not value:
        return ""

    if normalize_provider_id(str(provider or "")) != "ollama":
        return value

    try:
        parsed = urlparse(value)
    except Exception:
        return value

    # IMPORTANT: old Ollama configs may still store the root OpenAI-compat host.
    # Normalize only the empty-path form to `/v1`; do not rewrite custom subpaths.
    if parsed.path not in ("", "/"):
        return value

    return urlunparse(parsed._replace(path="/v1"))


def normalize_model_id(model: str) -> str:
    """Normalize model ID (resolve aliases)."""
    m = model.lower().strip()
    return MODEL_ALIASES.get(m, m)


def get_provider_info(provider: str) -> Optional[ProviderInfo]:
    """Get provider info by name."""
    return PROVIDER_CATALOG.get(provider.lower())


def list_providers() -> list:
    """List all available provider names."""
    return list(PROVIDER_CATALOG.keys())


def _normalize_host(host: str) -> str:
    return host.lower().strip().rstrip(".")


def is_loopback_host(host: str) -> bool:
    """Return True if host is one of the canonical loopback names."""
    return _normalize_host(host) in {"localhost", "127.0.0.1", "::1"}


def get_loopback_host_aliases(host: str) -> set[str]:
    """
    Return canonical loopback aliases when host is loopback.

    This intentionally returns all canonical aliases so validation remains stable
    regardless of whether callers use localhost, IPv4 loopback, or IPv6 loopback.
    """
    if not is_loopback_host(host):
        return set()
    return {"localhost", "127.0.0.1", "::1"}


def is_local_provider(provider: str) -> bool:
    """
    Return True for catalog providers intended for local-loopback use.

    Local providers are identified by:
    - no API key requirement, and
    - loopback default endpoint or explicit "(Local)" naming.
    """
    info = get_provider_info(provider)
    if not info:
        return False
    if info.env_key_name is not None:
        return False

    try:
        parsed = urlparse(info.base_url or "")
        host = parsed.hostname or ""
    except Exception:
        host = ""

    return is_loopback_host(host) or info.name.lower().endswith("(local)")


def get_default_public_llm_hosts() -> set[str]:
    """
    Return the default *public* LLM hosts that are safe to allow by default.

    Rationale:
    - We want built-in providers to work out-of-the-box without requiring users to
      configure an SSRF allowlist.
    - Custom Base URLs must still pass SSRF validation (host allowlist + public IP).
    - Local providers are intentionally excluded from this *public* allowlist.
      Their loopback behavior is handled by explicit provider-aware controls.
    """
    hosts: set[str] = set()

    for info in PROVIDER_CATALOG.values():
        if not info.base_url:
            continue
        try:
            parsed = urlparse(info.base_url)
        except Exception:
            continue

        if parsed.scheme != "https":
            continue

        host = parsed.hostname
        if not host:
            continue

        host = host.lower().rstrip(".")
        if host in ("localhost", "127.0.0.1", "::1"):
            continue

        hosts.add(host)

    return hosts
