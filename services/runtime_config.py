"""
Runtime Config Service (R21/S13/R70).
Manages non-secret LLM configuration with precedence, validation, and persistence.
R70: Strict settings registration + schema-coerced writes.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("ComfyUI-OpenClaw.services.runtime_config")

# S49: tenant context + namespace-aware config resolution.
try:
    from .tenant_context import (
        DEFAULT_TENANT_ID,
        get_current_tenant_id,
        is_multi_tenant_enabled,
        normalize_tenant_id,
    )
except ImportError:
    try:
        from services.tenant_context import (  # type: ignore
            DEFAULT_TENANT_ID,
            get_current_tenant_id,
            is_multi_tenant_enabled,
            normalize_tenant_id,
        )
    except ImportError:
        DEFAULT_TENANT_ID = "default"

        def get_current_tenant_id():  # type: ignore
            return DEFAULT_TENANT_ID

        def is_multi_tenant_enabled():  # type: ignore
            return False

        def normalize_tenant_id(value):  # type: ignore
            return str(value or DEFAULT_TENANT_ID).strip().lower() or DEFAULT_TENANT_ID


# R70: Settings schema registry (type coercion + unknown-key rejection)
try:
    from .settings_schema import coerce_dict as _schema_coerce
    from .settings_schema import get_schema_map
    from .settings_schema import is_registered as _schema_registered
except ImportError:
    try:
        from services.settings_schema import (
            coerce_dict as _schema_coerce,  # type: ignore
        )
        from services.settings_schema import get_schema_map
        from services.settings_schema import is_registered as _schema_registered
    except ImportError:
        # Fail-open: no schema enforcement if module missing
        def _schema_coerce(updates):  # type: ignore
            return updates, []

        def get_schema_map():  # type: ignore
            return {}

        def _schema_registered(key):  # type: ignore
            return True


# S66: Runtime guardrails (centralized ENV-driven runtime-only limits/diagnostics)
try:
    from .runtime_guardrails import (
        get_runtime_guardrails_snapshot,
        strip_runtime_only_config_fields,
    )
except ImportError:
    try:
        from services.runtime_guardrails import (  # type: ignore
            get_runtime_guardrails_snapshot,
            strip_runtime_only_config_fields,
        )
    except ImportError:
        # Compatibility fallback (should not happen in normal runtime/tests)
        def get_runtime_guardrails_snapshot(*, emit_audit: bool = False):  # type: ignore
            return {
                "status": "ok",
                "code": "S66_GUARDRAILS_OK",
                "deployment_profile": (
                    os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE") or "local"
                ),
                "runtime_profile": (
                    os.environ.get("OPENCLAW_RUNTIME_PROFILE") or "minimal"
                ),
                "runtime_only": True,
                "values": {
                    "retention": {
                        "job_event_buffer_size": 500,
                        "job_event_ttl_sec": 600,
                    },
                    "timeout_retry": {
                        "llm_timeout_cap_sec": 300,
                        "llm_max_retries_cap": 10,
                    },
                    "bounded_queues": {
                        "max_inflight_submits_total": 2,
                        "max_rendered_workflow_bytes": 512 * 1024,
                    },
                    "provider_safety": {
                        "allow_any_public_llm_host_default": False,
                        "allow_insecure_base_url_default": False,
                    },
                },
                "sources": {},
                "violations": [],
            }

        def strip_runtime_only_config_fields(config_blob):  # type: ignore
            return config_blob, []


# R139: Layered config resolver + compatibility env alias helpers.
try:
    from .config_layers import (
        ADMIN_TOKEN_ENV_KEYS,
        LLM_ENV_MAPPINGS,
        SOURCE_ENV,
        SOURCE_PERSISTED,
        SOURCE_RUNTIME_OVERRIDE,
    )
    from .config_layers import clear_runtime_overrides as _clear_runtime_overrides
    from .config_layers import get_first_present_env, get_preferred_env_value
    from .config_layers import get_runtime_overrides as _get_runtime_overrides
    from .config_layers import resolve_layered_config
    from .config_layers import set_runtime_overrides as _set_runtime_overrides
except ImportError:
    try:
        from services.config_layers import (
            ADMIN_TOKEN_ENV_KEYS,
            LLM_ENV_MAPPINGS,
            SOURCE_ENV,
            SOURCE_PERSISTED,
            SOURCE_RUNTIME_OVERRIDE,
        )
        from services.config_layers import (
            clear_runtime_overrides as _clear_runtime_overrides,  # type: ignore
        )
        from services.config_layers import (
            get_first_present_env,
            get_preferred_env_value,
        )
        from services.config_layers import (
            get_runtime_overrides as _get_runtime_overrides,
        )
        from services.config_layers import resolve_layered_config
        from services.config_layers import (
            set_runtime_overrides as _set_runtime_overrides,
        )
    except ImportError:
        # Compatibility fallback for constrained test environments.
        ADMIN_TOKEN_ENV_KEYS = ("OPENCLAW_ADMIN_TOKEN", "MOLTBOT_ADMIN_TOKEN")
        SOURCE_ENV = "env"
        SOURCE_PERSISTED = "persisted"
        SOURCE_RUNTIME_OVERRIDE = "runtime_override"
        LLM_ENV_MAPPINGS = {
            "provider": ("OPENCLAW_LLM_PROVIDER", "MOLTBOT_LLM_PROVIDER"),
            "model": ("OPENCLAW_LLM_MODEL", "MOLTBOT_LLM_MODEL"),
            "base_url": ("OPENCLAW_LLM_BASE_URL", "MOLTBOT_LLM_BASE_URL"),
            "timeout_sec": ("OPENCLAW_LLM_TIMEOUT", "MOLTBOT_LLM_TIMEOUT"),
            "max_retries": ("OPENCLAW_LLM_MAX_RETRIES", "MOLTBOT_LLM_MAX_RETRIES"),
            "fallback_models": (
                "OPENCLAW_FALLBACK_MODELS",
                "MOLTBOT_FALLBACK_MODELS",
            ),
            "fallback_providers": (
                "OPENCLAW_FALLBACK_PROVIDERS",
                "MOLTBOT_FALLBACK_PROVIDERS",
            ),
            "max_failover_candidates": (
                "OPENCLAW_MAX_FAILOVER_CANDIDATES",
                "MOLTBOT_MAX_FAILOVER_CANDIDATES",
            ),
        }

        def get_first_present_env(keys, *, env=None):  # type: ignore
            env_map = env or os.environ
            for key in keys:
                if key in env_map:
                    return env_map.get(key)
            return None

        def get_preferred_env_value(primary, legacy, *, env=None):  # type: ignore
            env_map = env or os.environ
            if primary in env_map:
                return env_map.get(primary), False
            if legacy and legacy in env_map:
                return env_map.get(legacy), True
            return None, False

        _RUNTIME_OVERRIDES: Dict[str, Dict[str, Any]] = {}

        def _get_runtime_overrides(section):  # type: ignore
            return dict(_RUNTIME_OVERRIDES.get(section, {}))

        def _set_runtime_overrides(section, updates):  # type: ignore
            current = dict(_RUNTIME_OVERRIDES.get(section, {}))
            for key, value in updates.items():
                if value is None:
                    current.pop(key, None)
                else:
                    current[key] = value
            if current:
                _RUNTIME_OVERRIDES[section] = current
            else:
                _RUNTIME_OVERRIDES.pop(section, None)
            return dict(current)

        def _clear_runtime_overrides(section, keys=None):  # type: ignore
            if keys is None:
                _RUNTIME_OVERRIDES.pop(section, None)
                return
            current = _RUNTIME_OVERRIDES.get(section)
            if not current:
                return
            for key in keys:
                current.pop(key, None)
            if not current:
                _RUNTIME_OVERRIDES.pop(section, None)

        def resolve_layered_config(  # type: ignore
            *,
            ordered_keys,
            defaults,
            persisted=None,
            runtime_overrides=None,
            env_getter=None,
            normalize_value=None,
        ):
            persisted = dict(persisted or {})
            runtime_overrides = dict(runtime_overrides or {})
            effective = {}
            sources = {}
            for key in ordered_keys:
                value = defaults.get(key)
                source = "default"
                if key in persisted:
                    value = persisted.get(key)
                    source = SOURCE_PERSISTED
                if key in runtime_overrides:
                    value = runtime_overrides.get(key)
                    source = SOURCE_RUNTIME_OVERRIDE
                if env_getter is not None:
                    env_value = env_getter(key)
                    if env_value is not None:
                        value = env_value
                        source = SOURCE_ENV
                if normalize_value is not None:
                    value = normalize_value(key, value, source)
                effective[key] = value
                sources[key] = source
            return effective, sources


# Config file location (under state dir)
try:
    # Prefer package-relative imports when running as a ComfyUI custom node pack.
    from .state_dir import get_state_dir

    CONFIG_FILE = os.path.join(get_state_dir(), "config.json")
    from .providers.catalog import (
        PROVIDER_CATALOG,
        get_default_public_llm_hosts,
        get_loopback_host_aliases,
        is_local_provider,
    )
    from .safe_io import SSRFError, is_private_ip, validate_outbound_url
except ImportError:
    try:
        # Fallback for direct sys.path imports (unit tests / scripts)
        from services.state_dir import get_state_dir  # type: ignore

        CONFIG_FILE = os.path.join(get_state_dir(), "config.json")
        from services.providers.catalog import (  # type: ignore
            PROVIDER_CATALOG,
            get_default_public_llm_hosts,
            get_loopback_host_aliases,
            is_local_provider,
        )
        from services.safe_io import is_private_ip  # type: ignore
        from services.safe_io import SSRFError, validate_outbound_url
    except ImportError:
        CONFIG_FILE = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "config.json"
        )
        # Fallback to empty if missing
        PROVIDER_CATALOG = {}
        get_default_public_llm_hosts = lambda: set()  # type: ignore
        get_loopback_host_aliases = lambda _host: set()  # type: ignore
        is_local_provider = lambda _provider: False  # type: ignore

        # Mock for validation if missing (Fail Closed)
        class SSRFError(ValueError):
            pass

        def validate_outbound_url(url, **kwargs):
            raise SSRFError(
                "Security dependencies missing: Cannot validate URL safety."
            )

        def is_private_ip(ip):
            return True  # Assume unsafe if missing


# Allowed config keys (whitelist)
ALLOWED_LLM_KEYS = {
    "provider",
    "model",
    "base_url",
    "timeout_sec",
    "max_retries",
    # R14: Failover config
    "fallback_models",
    "fallback_providers",
    "max_failover_candidates",
}

ALLOWED_SCHEDULER_KEYS = {
    "startup_jitter_sec",
    "max_runs_per_tick",
    "skip_missed_intervals",
    "execution_mode",
    "compute_error_disable_threshold",
}

# Default values
DEFAULTS = {
    "llm": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "",
        "timeout_sec": 120,
        "max_retries": 3,
        # R14: Failover defaults (empty = disabled)
        "fallback_models": [],
        "fallback_providers": [],
        "max_failover_candidates": 3,
    },
    "scheduler": {
        "startup_jitter_sec": 30,
        "max_runs_per_tick": 5,
        "skip_missed_intervals": False,
        "execution_mode": "auto",
        "compute_error_disable_threshold": 3,
    },
}

# Value constraints
CONSTRAINTS = {
    "timeout_sec": (5, 300),
    "max_retries": (0, 10),
    "max_failover_candidates": (1, 5),  # R14: Limit total candidates
}

SCHEDULER_CONSTRAINTS = {
    "startup_jitter_sec": (0, 300),
    "max_runs_per_tick": (1, 100),
    "compute_error_disable_threshold": (1, 20),
}

# Environment variable mappings (new, legacy)
# R139: defined in services.config_layers as the single source-of-truth.
ENV_MAPPINGS = dict(LLM_ENV_MAPPINGS)

SCHEDULER_ENV_MAPPINGS = {
    "startup_jitter_sec": ("OPENCLAW_SCHEDULER_STARTUP_JITTER_SEC", ""),
    "max_runs_per_tick": ("OPENCLAW_SCHEDULER_MAX_RUNS_PER_TICK", ""),
    "skip_missed_intervals": ("OPENCLAW_SCHEDULER_SKIP_MISSED", ""),
    "execution_mode": ("OPENCLAW_SCHEDULER_EXECUTION_MODE", ""),
    "compute_error_disable_threshold": (
        "OPENCLAW_SCHEDULER_COMPUTE_ERROR_DISABLE_THRESHOLD",
        "",
    ),
}

# IMPORTANT:
# Keep effective-config merge order deterministic.
# Using a set iteration here makes legacy warning assertions flaky because the
# first env key read can vary per process/hash seed.
LLM_KEY_ORDER = tuple(ENV_MAPPINGS.keys())


def _clamp(value: int, min_val: int, max_val: int) -> int:
    """Clamp an integer to a range."""
    return max(min_val, min(max_val, value))


def _s66_timeout_retry_caps() -> Tuple[int, int]:
    """Return dynamic caps for timeout/retry, bounded by legacy constraints."""
    snapshot = get_runtime_guardrails_snapshot()
    timeout_caps = snapshot.get("values", {}).get("timeout_retry", {})
    timeout_cap = int(
        timeout_caps.get("llm_timeout_cap_sec", CONSTRAINTS["timeout_sec"][1])
    )
    retry_cap = int(
        timeout_caps.get("llm_max_retries_cap", CONSTRAINTS["max_retries"][1])
    )
    timeout_cap = min(timeout_cap, CONSTRAINTS["timeout_sec"][1])
    retry_cap = min(retry_cap, CONSTRAINTS["max_retries"][1])
    return timeout_cap, retry_cap


def _get_constraint_range(key: str) -> Tuple[int, int]:
    """Resolve value constraint range, applying S66 runtime caps where relevant."""
    min_val, max_val = CONSTRAINTS[key]
    if key == "timeout_sec":
        timeout_cap, _ = _s66_timeout_retry_caps()
        max_val = min(max_val, timeout_cap)
    elif key == "max_retries":
        _, retry_cap = _s66_timeout_retry_caps()
        max_val = min(max_val, retry_cap)
    return min_val, max_val


def _resolve_active_tenant_id(tenant_id: Optional[str] = None) -> str:
    if not is_multi_tenant_enabled():
        return DEFAULT_TENANT_ID
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    try:
        return normalize_tenant_id(tenant_id)
    except Exception:
        return DEFAULT_TENANT_ID


def _allow_tenant_config_fallback() -> bool:
    value = (
        os.environ.get("OPENCLAW_MULTI_TENANT_ALLOW_CONFIG_FALLBACK")
        or os.environ.get("MOLTBOT_MULTI_TENANT_ALLOW_CONFIG_FALLBACK")
        or "0"
    )
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _runtime_override_section(tenant_id: Optional[str] = None) -> str:
    resolved = _resolve_active_tenant_id(tenant_id)
    if resolved == DEFAULT_TENANT_ID:
        return "llm"
    return f"llm::{resolved}"


def _tenant_llm_config_view(
    config_blob: Dict[str, Any], tenant_id: str
) -> Dict[str, Any]:
    llm_global = config_blob.get("llm", {})
    if tenant_id == DEFAULT_TENANT_ID:
        return llm_global if isinstance(llm_global, dict) else {}

    tenants = config_blob.get("tenants", {})
    tenant_cfg = {}
    if isinstance(tenants, dict):
        tenant_cfg = tenants.get(tenant_id, {})
    tenant_llm = tenant_cfg.get("llm", {}) if isinstance(tenant_cfg, dict) else {}
    if isinstance(tenant_llm, dict) and tenant_llm:
        return tenant_llm
    if _allow_tenant_config_fallback() and isinstance(llm_global, dict):
        return llm_global
    return {}


def _load_file_config() -> Dict[str, Any]:
    """Load config from file if exists."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                sanitized, notices = strip_runtime_only_config_fields(raw)
                if notices:
                    logger.warning(
                        "S66: Ignoring runtime-only guardrail keys from persisted config (%s)",
                        ", ".join(n.get("path", "?") for n in notices),
                    )
                return sanitized
            return {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load config file: {e}")
    return {}


def _save_file_config(config: Dict[str, Any]) -> bool:
    """Save config to file."""
    try:
        config_to_save, notices = strip_runtime_only_config_fields(config)
        if notices:
            logger.warning(
                "S66: Stripped runtime-only guardrail keys before config save (%s)",
                ", ".join(n.get("path", "?") for n in notices),
            )
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_to_save, f, indent=2)
        logger.info(f"Saved config to {CONFIG_FILE}")
        return True
    except OSError as e:
        logger.error(f"Failed to save config file: {e}")
        return False


def _get_env_value(key: str) -> Optional[str]:
    """
    Get environment variable value for a config key (prefers new names, falls back to legacy).
    Logs a warning exactly once per key if legacy variable is used.
    """
    env_vars = ENV_MAPPINGS.get(key)
    if not env_vars:
        return None
    primary, legacy = env_vars
    value, used_legacy = get_preferred_env_value(primary, legacy)
    if not used_legacy:
        return value

    # Check if we've already warned for this key to avoid spam.
    if not getattr(_get_env_value, "_warned_legacy", None):
        _get_env_value._warned_legacy = set()

    if legacy not in _get_env_value._warned_legacy:
        logger.warning(
            f"Config: Using legacy environment variable {legacy}. "
            f"Please update to {primary} in future versions."
        )
        _get_env_value._warned_legacy.add(legacy)
    return value


def _env_flag(primary: str, legacy: str, default: bool = False) -> bool:
    """
    Boolean env helper with new/legacy names.
    Accepts: 1/true/yes/on (case-insensitive) as True.
    """
    if primary in os.environ:
        v = os.environ.get(primary, "")
    elif legacy in os.environ:
        v = os.environ.get(legacy, "")
    else:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def get_llm_egress_controls(provider: str, base_url: str) -> Dict[str, Any]:
    """
    Build canonical outbound SSRF controls for LLM egress paths.

    IMPORTANT:
    Callers must reuse this same control set for both pre-validation and request-time
    validation. Diverging parameters caused the S65 loopback regression (pre-check
    passed while request-time check failed with HTTP 403).
    """
    allowed_hosts_str = os.environ.get("OPENCLAW_LLM_ALLOWED_HOSTS") or os.environ.get(
        "MOLTBOT_LLM_ALLOWED_HOSTS", ""
    )
    env_hosts = {h.lower().strip() for h in allowed_hosts_str.split(",") if h.strip()}
    allowed_hosts = set(get_default_public_llm_hosts()) | env_hosts

    guardrails = get_runtime_guardrails_snapshot()
    provider_safety = guardrails.get("values", {}).get("provider_safety", {})
    default_allow_any = bool(
        provider_safety.get("allow_any_public_llm_host_default", False)
    )
    allow_any = _env_flag(
        "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST",
        "MOLTBOT_ALLOW_ANY_PUBLIC_LLM_HOST",
        default=default_allow_any,
    )

    allow_loopback_hosts: Optional[set[str]] = None
    try:
        host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    except Exception:
        host = ""

    # CRITICAL:
    # Local providers can use loopback only. Never widen this to blanket private IPs;
    # doing so would reopen SSRF paths into internal networks.
    if host and is_local_provider(provider):
        loopback_aliases = get_loopback_host_aliases(host)
        if loopback_aliases:
            allow_loopback_hosts = loopback_aliases
            allowed_hosts |= loopback_aliases

    return {
        "allow_hosts": None if allow_any else allowed_hosts,
        "allow_any_public_host": allow_any,
        "allow_loopback_hosts": allow_loopback_hosts,
    }


def get_scheduler_config() -> Dict[str, Any]:
    """
    Get effective Scheduler config (Env > Defaults).
    Note: Scheduler config is currently not persisted to file (Env only).
    """
    effective = {}
    defaults = DEFAULTS["scheduler"]

    for key in ALLOWED_SCHEDULER_KEYS:
        # Check ENV
        env_vars = SCHEDULER_ENV_MAPPINGS.get(key)
        if env_vars:
            primary, _ = env_vars
            val = os.environ.get(primary)

            if val is not None:
                # Parse
                if key == "skip_missed_intervals":
                    effective[key] = str(val).strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                elif key in SCHEDULER_CONSTRAINTS:
                    try:
                        val_int = int(val)
                        effective[key] = _clamp(val_int, *SCHEDULER_CONSTRAINTS[key])
                    except ValueError:
                        effective[key] = defaults[key]
                else:
                    effective[key] = val
                continue

        # Use default
        effective[key] = defaults.get(key)

    return effective


def _normalize_llm_layer_value(key: str, value: Any, source: str) -> Any:
    """Normalize/clamp per-key values while preserving compatibility semantics."""
    if source == SOURCE_ENV:
        if key in ("fallback_models", "fallback_providers"):
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        if key in CONSTRAINTS:
            try:
                value_int = int(value)
            except (TypeError, ValueError):
                return DEFAULTS["llm"].get(key)
            min_val, max_val = _get_constraint_range(key)
            return _clamp(value_int, min_val, max_val)

        return value

    # Persisted/runtime/default values keep historical compatibility:
    # clamp numeric constraint keys only when the value is already numeric.
    if key in CONSTRAINTS and isinstance(value, (int, float)):
        min_val, max_val = _get_constraint_range(key)
        return _clamp(int(value), min_val, max_val)
    return value


def get_runtime_overrides(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """Get current in-memory runtime overrides for the LLM section."""
    return _get_runtime_overrides(_runtime_override_section(tenant_id))


def set_runtime_overrides(
    updates: Dict[str, Any], tenant_id: Optional[str] = None
) -> Tuple[bool, list]:
    """
    Set in-memory runtime overrides for LLM config (non-persisted).

    Uses the same schema validation path as persisted config updates.
    """
    sanitized, errors = validate_config_update(updates)
    if errors:
        return False, errors
    _set_runtime_overrides(_runtime_override_section(tenant_id), sanitized)
    return True, []


def clear_runtime_overrides(
    keys: Optional[List[str]] = None, tenant_id: Optional[str] = None
) -> None:
    """Clear all runtime overrides (or only selected keys) for LLM config."""
    _clear_runtime_overrides(_runtime_override_section(tenant_id), keys=keys)


def get_effective_config(
    tenant_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Get effective LLM config with precedence:
    ENV > runtime_override > persisted file > defaults.

    Returns:
        Tuple of (effective_config, sources) where sources maps each key to its origin.
    """
    active_tenant = _resolve_active_tenant_id(tenant_id)
    file_blob = _load_file_config()
    file_config = _tenant_llm_config_view(file_blob, active_tenant)
    runtime_overrides = get_runtime_overrides(active_tenant)

    ordered_keys = list(LLM_KEY_ORDER) + [
        k for k in sorted(ALLOWED_LLM_KEYS) if k not in ENV_MAPPINGS
    ]
    return resolve_layered_config(
        ordered_keys=ordered_keys,
        defaults=DEFAULTS["llm"],
        persisted=file_config,
        runtime_overrides=runtime_overrides,
        env_getter=_get_env_value,
        normalize_value=_normalize_llm_layer_value,
    )


def get_settings_schema() -> dict:
    """R70: Return the full settings schema map for frontend consumption."""
    return get_schema_map()


def get_runtime_guardrails() -> Dict[str, Any]:
    """S66: Return centralized runtime guardrails diagnostics snapshot."""
    return get_runtime_guardrails_snapshot()


def validate_config_update(updates: Dict[str, Any]) -> Tuple[Dict[str, Any], list]:
    """
    Validate and sanitize config updates.
    R70: Schema-coerced writes — types are coerced before any domain validation.

    Returns:
        Tuple of (sanitized_updates, errors)
    """
    sanitized = {}
    errors = []

    # R70: Phase 1 — Schema coercion (unknown keys rejected here)
    coerced, coercion_errors = _schema_coerce(updates)
    if coercion_errors:
        errors.extend(coercion_errors)

    for key, val in coerced.items():
        # Belt-and-suspenders: also check legacy whitelist
        if key not in ALLOWED_LLM_KEYS:
            errors.append(f"Unknown key: {key}")
            continue

        # Validate types and constraints (post-coercion, values should already be typed)
        if key in CONSTRAINTS:
            if not isinstance(val, (int, float)):
                errors.append(f"{key} must be a number")
                continue
            min_val, max_val = _get_constraint_range(key)
            val = _clamp(int(val), min_val, max_val)
        elif key == "provider":
            if not isinstance(val, str):
                errors.append("provider must be a string")
                continue
            # R16: Validate against known providers from catalog
            # R73: Normalize provider aliases before validation
            try:
                from .providers.catalog import list_providers, normalize_provider_id

                val = normalize_provider_id(val)
                valid_providers = set(list_providers())
            except ImportError:
                try:
                    from services.providers.catalog import (  # type: ignore
                        list_providers,
                        normalize_provider_id,
                    )

                    val = normalize_provider_id(val)
                    valid_providers = set(list_providers())
                except ImportError:
                    valid_providers = {
                        "openai",
                        "anthropic",
                        "openrouter",
                        "gemini",
                        "groq",
                        "deepseek",
                        "xai",
                        "ollama",
                        "lmstudio",
                        "custom",
                    }

            if val not in valid_providers:
                errors.append(f"Unknown provider: {val}")
                continue
        elif key == "base_url":
            if not isinstance(val, str):
                errors.append("base_url must be a string")
                continue
            # NOTE: Allow empty base_url (use provider default).
            # Without this, UI saves can fail with "Invalid scheme" on blank base_url.
            if val.strip() == "":
                sanitized[key] = ""
                continue
            # S16: Base URL policy

            # 1. Allow if it matches the *default* base_url for the selected provider
            # R73 FIX: Use the already-normalized provider from sanitized (post
            # normalize_provider_id), so alias providers like "local" → "lmstudio"
            # hit the correct local-provider branch.
            provider_key = sanitized.get(
                "provider",
                coerced.get("provider", updates.get("provider", "custom")),
            )
            if isinstance(provider_key, str):
                provider_key = provider_key.lower()
            else:
                provider_key = "custom"
            known_provider = PROVIDER_CATALOG.get(provider_key)

            if known_provider and val == known_provider.base_url:
                # Matches known good default
                pass

            else:
                # Custom URL (either custom provider OR overriding default URL).

                # Check opt-in for custom URLs
                if provider_key == "custom" and not _env_flag(
                    "OPENCLAW_ALLOW_CUSTOM_BASE_URL",
                    "MOLTBOT_ALLOW_CUSTOM_BASE_URL",
                    default=False,
                ):
                    errors.append(
                        "Custom Base URL requires OPENCLAW_ALLOW_CUSTOM_BASE_URL=1 (or legacy MOLTBOT_ALLOW_CUSTOM_BASE_URL=1)"
                    )
                    continue

                controls = get_llm_egress_controls(provider_key, val)

                # Keep local providers strict: only loopback endpoints are acceptable.
                if is_local_provider(provider_key) and not controls.get(
                    "allow_loopback_hosts"
                ):
                    errors.append(
                        f"Local provider {provider_key} must use localhost URL"
                    )
                    continue

                try:
                    from .safe_io import STANDARD_OUTBOUND_POLICY

                    validate_outbound_url(
                        val,
                        allow_hosts=controls.get("allow_hosts"),
                        allow_any_public_host=bool(
                            controls.get("allow_any_public_host")
                        ),
                        allow_loopback_hosts=controls.get("allow_loopback_hosts"),
                        policy=STANDARD_OUTBOUND_POLICY,
                    )
                except SSRFError as e:
                    # Allow override via insecure flag (legacy/risk acceptance)
                    if not _env_flag(
                        "OPENCLAW_ALLOW_INSECURE_BASE_URL",
                        "MOLTBOT_ALLOW_INSECURE_BASE_URL",
                        default=False,
                    ):
                        errors.append(
                            "Unsafe Base URL blocked (SSRF): "
                            f"{e}. OPENCLAW_LLM_ALLOWED_HOSTS "
                            "(or legacy MOLTBOT_LLM_ALLOWED_HOSTS) only allows "
                            "additional exact public hosts; private/reserved IP "
                            "targets still require "
                            "OPENCLAW_ALLOW_INSECURE_BASE_URL=1. Wildcard '*' "
                            "entries are not supported."
                        )
                        continue
        elif key == "model":
            if not isinstance(val, str):
                errors.append("model must be a string")
                continue

        sanitized[key] = val

    return sanitized, errors


def get_apply_semantics(updated_keys: list) -> Dict[str, list]:
    """
    R53: Determine apply semantics for updated keys.
    Returns:
        {
            "applied_now": [keys applied immediately],
            "restart_required": [keys requiring restart],
            "notes": [explanatory notes]
        }
    """
    applied_now = []
    restart_required = []
    notes = []

    for key in updated_keys:
        if key in ALLOWED_LLM_KEYS:
            # LLM keys are read from file on every request (via get_effective_config),
            # so they are effectively "applied now".
            applied_now.append(key)
        elif key in ALLOWED_SCHEDULER_KEYS:
            # Scheduler config is env-only (not file-based) in current implementation,
            # but if it were updateable via API, it might require restart or re-init.
            # For now, this path is unused by config_put_handler which targets LLM config.
            restart_required.append(key)
            notes.append(f"{key} requires service restart to take effect.")
        else:
            # Unknown keys? Assume restart needed for safety if they slipped through validation
            restart_required.append(key)

    return {
        "applied_now": sorted(applied_now),
        "restart_required": sorted(restart_required),
        "notes": notes,
    }


def _merge_config_value(base: Any, patch: Any, key: str = "") -> Any:
    """
    R94: Non-destructive merge for a single config value.

    Rules:
    - Both dicts → recursive merge
    - Both lists of objects with "id" keys → merge-by-id (update matched, append new)
    - Base is id-keyed list but patch is not → keep base (log warning)
    - All other cases → patch overwrites base
    """
    # Dict merge
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for k, v in patch.items():
            merged[k] = _merge_config_value(merged.get(k), v, key=k)
        return merged

    # List merge-by-id
    if isinstance(base, list) and isinstance(patch, list):
        # Check if base is id-keyed (all dicts with "id")
        base_is_id_keyed = len(base) > 0 and all(
            isinstance(item, dict) and "id" in item for item in base
        )
        if base_is_id_keyed:
            patch_is_id_keyed = all(
                isinstance(item, dict) and "id" in item for item in patch
            )
            if not patch_is_id_keyed:
                logger.warning(
                    f"R94: Config key '{key}' base is id-keyed but patch is not. "
                    f"Keeping base array to prevent destructive replacement."
                )
                return base

            # Merge by id
            merged_map: Dict[str, Any] = {item["id"]: dict(item) for item in base}
            for patch_item in patch:
                pid = patch_item["id"]
                if pid in merged_map:
                    # Update existing entry (shallow merge)
                    merged_map[pid].update(patch_item)
                else:
                    # Append new entry
                    merged_map[pid] = dict(patch_item)
            return list(merged_map.values())

        # Non-id-keyed: patch overwrites
        return patch

    # All other types: patch overwrites
    return patch


def update_config(
    updates: Dict[str, Any], tenant_id: Optional[str] = None
) -> Tuple[bool, list]:
    """
    Update LLM config, persisting to file.

    Returns:
        Tuple of (success, errors)
    """
    sanitized, errors = validate_config_update(updates)

    if errors:
        return False, errors

    if not sanitized:
        return True, []  # Nothing to update

    tenant_id = _resolve_active_tenant_id(tenant_id)

    # R94/S49: Non-destructive merge with existing file config
    file_config = _load_file_config()
    if tenant_id == DEFAULT_TENANT_ID:
        if "llm" not in file_config:
            file_config["llm"] = {}
        target = file_config["llm"]
    else:
        tenants = file_config.get("tenants")
        if not isinstance(tenants, dict):
            tenants = {}
            file_config["tenants"] = tenants
        tenant_cfg = tenants.get(tenant_id)
        if not isinstance(tenant_cfg, dict):
            tenant_cfg = {}
            tenants[tenant_id] = tenant_cfg
        if "llm" not in tenant_cfg or not isinstance(tenant_cfg.get("llm"), dict):
            tenant_cfg["llm"] = {}
        target = tenant_cfg["llm"]

    for k, v in sanitized.items():
        target[k] = _merge_config_value(target.get(k), v, key=k)

    if _save_file_config(file_config):
        logger.info("Updated config: %s (tenant=%s)", list(sanitized.keys()), tenant_id)
        return True, []
    else:
        return False, ["Failed to save config file"]


def is_config_write_enabled() -> bool:
    """
    Backwards-compat shim.
    Config writes are no longer gated by a separate "enable" flag; admin access policy controls writes.
    """
    return True


def validate_admin_token(token: str) -> bool:
    """Validate admin token for config writes (S13)."""
    expected = get_first_present_env(ADMIN_TOKEN_ENV_KEYS) or ""
    if not expected:
        return True  # No token configured = convenience mode; caller must still enforce loopback-only.
    return token == expected


def get_admin_token() -> str:
    """
    Returns the configured admin token (preferred OPENCLAW, legacy MOLTBOT) or "" if not configured.

    This is for internal policy decisions only (e.g., "is a token configured?").
    Never return this value to UI callers and never log it.
    """
    return get_first_present_env(ADMIN_TOKEN_ENV_KEYS) or ""


def is_loopback_client(remote_addr: str) -> bool:
    """Check if client is from loopback address."""
    return remote_addr in ("127.0.0.1", "::1", "localhost")


class RuntimeConfig:
    """
    Typed configuration snapshot.
    Aggregates effective settings from layered config sources.
    """

    def __init__(self):
        # LLM Settings
        self.llm, _ = get_effective_config()
        self.runtime_guardrails = get_runtime_guardrails_snapshot()

        # Feature Flags
        self.bridge_enabled = _env_flag(
            "OPENCLAW_BRIDGE_ENABLED", "MOLTBOT_BRIDGE_ENABLED", False
        )

        # Security Flags (S41)
        self.allow_any_public_llm_host = _env_flag(
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST",
            "MOLTBOT_ALLOW_ANY_PUBLIC_LLM_HOST",
            False,
        )
        self.allow_insecure_base_url = _env_flag(
            "OPENCLAW_ALLOW_INSECURE_BASE_URL", "MOLTBOT_ALLOW_INSECURE_BASE_URL", False
        )
        self.webhook_auth_mode = os.environ.get("OPENCLAW_WEBHOOK_AUTH_MODE", "")
        self.security_dangerous_bind_override = _env_flag(
            "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE",
            "MOLTBOT_SECURITY_DANGEROUS_BIND_OVERRIDE",
            False,
        )
        self.admin_token_configured = bool(get_admin_token())


def get_config() -> RuntimeConfig:
    """Factory to get current config snapshot."""
    return RuntimeConfig()
