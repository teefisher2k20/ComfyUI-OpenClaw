"""
Config API handlers (R21/S13/F20).
Provides GET/PUT /moltbot/config and optional /moltbot/llm/test.
"""

from __future__ import annotations

import json
import logging

if __package__ and "." in __package__:
    from ..services.import_fallback import import_attrs_dual, import_module_dual
else:
    from services.import_fallback import (  # type: ignore
        import_attrs_dual,
        import_module_dual,
    )

try:
    (PACK_VERSION,) = import_attrs_dual(
        __package__,
        "..config",
        "config",
        ("PACK_VERSION",),
    )
except ImportError:  # pragma: no cover
    PACK_VERSION = "0.1.0"

try:
    from aiohttp import web
except ImportError:  # pragma: no cover (optional for unit tests)
    # CRITICAL test/CI fallback:
    # Some CI/unit environments intentionally run without aiohttp installed.
    # Keep this module importable by providing a minimal `web` shim used by
    # handler tests (json_response/status/body), while production keeps real aiohttp.
    class _MockResponse:
        def __init__(
            self, payload: dict, status: int = 200, headers: dict | None = None
        ):
            self.status = status
            self.headers = headers or {}
            self.body = json.dumps(payload).encode("utf-8")

    class _MockWeb:
        _IS_MOCKWEB = True

        class Request:  # pragma: no cover - typing shim only
            pass

        class Response:  # pragma: no cover - typing shim only
            pass

        @staticmethod
        def json_response(
            payload: dict, status: int = 200, headers: dict | None = None
        ):
            return _MockResponse(payload, status=status, headers=headers)

    web = _MockWeb()  # type: ignore

# Import discipline:
# - In real ComfyUI runtimes, this pack is loaded as a package and must use package-relative imports.
# - In unit tests, modules may be imported as top-level (e.g., `api.*`), so we allow top-level fallbacks.
(
    is_loopback,
    require_admin_token,
    require_observability_access,
    resolve_token_info,
) = import_attrs_dual(
    __package__,
    "..services.access_control",
    "services.access_control",
    (
        "is_loopback",
        "require_admin_token",
        "require_observability_access",
        "resolve_token_info",
    ),
)
(emit_audit_event,) = import_attrs_dual(
    __package__,
    "..services.audit",
    "services.audit",
    ("emit_audit_event",),
)

try:
    (require_same_origin_if_no_token,) = import_attrs_dual(
        __package__,
        "..services.csrf_protection",
        "services.csrf_protection",
        ("require_same_origin_if_no_token",),
    )
except Exception:
    # CRITICAL test/CI fallback (DO NOT replace with a direct import):
    # Some unit-test environments import `api.config` without aiohttp installed.
    # `services.csrf_protection` imports aiohttp at module load, which can raise
    # ModuleNotFoundError and break unrelated tests (`test_r53`, `test_r60`).
    # Keep import-time behavior resilient by using a no-op guard in that case.
    def require_same_origin_if_no_token(*_args, **_kwargs):  # type: ignore
        return None


(LLMClient,) = import_attrs_dual(
    __package__,
    "..services.llm_client",
    "services.llm_client",
    ("LLMClient",),
)
(check_rate_limit,) = import_attrs_dual(
    __package__,
    "..services.rate_limit",
    "services.rate_limit",
    ("check_rate_limit",),
)
(get_client_ip,) = import_attrs_dual(
    __package__,
    "..services.request_ip",
    "services.request_ip",
    ("get_client_ip",),
)
(
    ALLOWED_LLM_KEYS,
    get_admin_token,
    get_apply_semantics,
    get_effective_config,
    get_llm_egress_controls,
    get_runtime_guardrails,
    get_settings_schema,
    is_loopback_client,
    update_config,
) = import_attrs_dual(
    __package__,
    "..services.runtime_config",
    "services.runtime_config",
    (
        "ALLOWED_LLM_KEYS",
        "get_admin_token",
        "get_apply_semantics",
        "get_effective_config",
        "get_llm_egress_controls",
        "get_runtime_guardrails",
        "get_settings_schema",
        "is_loopback_client",
        "update_config",
    ),
)
(
    TenantBoundaryError,
    request_tenant_scope,
) = import_attrs_dual(
    __package__,
    "..services.tenant_context",
    "services.tenant_context",
    ("TenantBoundaryError", "request_tenant_scope"),
)
(
    CODE_RUNTIME_ONLY_PERSIST_FORBIDDEN,
    payload_contains_runtime_guardrails,
) = import_attrs_dual(
    __package__,
    "..services.runtime_guardrails",
    "services.runtime_guardrails",
    ("CODE_RUNTIME_ONLY_PERSIST_FORBIDDEN", "payload_contains_runtime_guardrails"),
)

logger = logging.getLogger("ComfyUI-OpenClaw.api.config")

(
    _MODEL_LIST_CACHE,
    _MODEL_LIST_MAX_ENTRIES,
    _MODEL_LIST_TTL_SEC,
    _build_model_cache_key,
    _cache_get,
    _cache_put,
    _extract_models_from_payload,
    _format_llm_ssrf_error,
    _get_llm_allowed_hosts,
    _llm_insecure_override_enabled,
    fetch_remote_model_list,
    get_stale_cached_models,
    resolve_model_list_target,
    validate_model_list_target,
) = import_attrs_dual(
    __package__,
    "..services.llm_model_list",
    "services.llm_model_list",
    (
        "_MODEL_LIST_CACHE",
        "_MODEL_LIST_MAX_ENTRIES",
        "_MODEL_LIST_TTL_SEC",
        "build_model_cache_key",
        "cache_get",
        "cache_put",
        "extract_models_from_payload",
        "format_llm_ssrf_error",
        "get_llm_allowed_hosts",
        "llm_insecure_override_enabled",
        "fetch_remote_model_list",
        "get_stale_cached_models",
        "resolve_model_list_target",
        "validate_model_list_target",
    ),
)


# S14/R98 / R64: Import Endpoint Metadata
(
    AuthTier,
    RiskTier,
    RoutePlane,
    endpoint_metadata,
) = import_attrs_dual(
    __package__,
    "..services.endpoint_manifest",
    "services.endpoint_manifest",
    ("AuthTier", "RiskTier", "RoutePlane", "endpoint_metadata"),
)


# Provider catalog for UI dropdown (R16 dynamic)
PROVIDER_CATALOG = []

try:
    raw_catalog_module = import_module_dual(
        __package__,
        "..services.providers.catalog",
        "services.providers.catalog",
    )
    RAW_CATALOG = raw_catalog_module.PROVIDER_CATALOG

    for pid, info in RAW_CATALOG.items():
        PROVIDER_CATALOG.append(
            {
                "id": pid,
                "label": info.name,
                "requires_key": info.env_key_name is not None,
            }
        )
    # Ensure custom is present if not in catalog (though it is)
    if not any(p["id"] == "custom" for p in PROVIDER_CATALOG):
        PROVIDER_CATALOG.append(
            {"id": "custom", "label": "Custom OpenAI-compatible", "requires_key": True}
        )
except ImportError:
    # Fallback if catalog module missing
    PROVIDER_CATALOG = [
        {"id": "openai", "label": "OpenAI", "requires_key": True},
        {"id": "anthropic", "label": "Anthropic", "requires_key": True},
        {"id": "openrouter", "label": "OpenRouter", "requires_key": True},
        {"id": "gemini", "label": "Google Gemini", "requires_key": True},
        {"id": "groq", "label": "Groq", "requires_key": True},
        {"id": "deepseek", "label": "DeepSeek", "requires_key": True},
        {"id": "xai", "label": "xAI (Grok)", "requires_key": True},
        {"id": "ollama", "label": "Ollama (Local)", "requires_key": False},
        {"id": "lmstudio", "label": "LM Studio (Local)", "requires_key": False},
        {"id": "custom", "label": "Custom OpenAI-compatible", "requires_key": True},
    ]


@endpoint_metadata(
    auth=AuthTier.OBSERVABILITY,
    risk=RiskTier.LOW,
    summary="Get configuration",
    description="Returns effective config, sources, and provider catalog.",
    audit="config.read",
    plane=RoutePlane.ADMIN,
)
async def config_get_handler(request: web.Request) -> web.Response:
    """
    GET /moltbot/config
    Returns effective config, sources, and provider catalog.
    Enforced by S14 Access Control.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    # S14: Access Control
    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            effective, sources = get_effective_config(tenant_id=tenant.tenant_id)
            guardrails = get_runtime_guardrails()
            if guardrails.get("status") != "ok":
                emit_audit_event(
                    action="runtime.guardrails",
                    target="runtime_guardrails",
                    outcome="warn",
                    token_info=token_info,
                    status_code=200,
                    details={
                        "tenant_id": tenant.tenant_id,
                        "code": guardrails.get("code"),
                        "violations": guardrails.get("violations", []),
                    },
                    request=request,
                )

            return web.json_response(
                {
                    "ok": True,
                    "tenant_id": tenant.tenant_id,
                    "config": effective,
                    "sources": sources,
                    "runtime_guardrails": guardrails,
                    "providers": PROVIDER_CATALOG,
                    # R70: Settings schema for frontend type coercion / validation
                    "schema": get_settings_schema(),
                    # Simplified UX: writes are controlled by admin access policy, not a separate env "enable" flag.
                    "write_enabled": True,
                }
            )
    except TenantBoundaryError as e:
        return web.json_response(
            {"ok": False, "error": e.code, "message": str(e)},
            status=403,
        )
    except Exception as e:
        logger.exception("Error getting config")
        return web.json_response(
            {
                "ok": False,
                "error": str(e),
            },
            status=500,
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,  # Read-only external fetch, but admin-gated
    summary="List remote models",
    description="Fetch a remote model list (best-effort) for OpenAI-compatible providers.",
    audit="llm.list_models",
    plane=RoutePlane.ADMIN,
)
async def llm_models_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/llm/models (legacy: /moltbot/llm/models)
    Fetch a remote model list (best-effort) for OpenAI-compatible providers.

    Security:
    - admin boundary
    - loopback-only unless OPENCLAW_ALLOW_REMOTE_ADMIN=1
    - SSRF policy enforced via OPENCLAW_LLM_ALLOWED_HOSTS / OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            # Admin boundary
            allowed, err = require_admin_token(request)
            if not allowed:
                emit_audit_event(
                    action="config.update",
                    target="config.json",
                    outcome="deny",
                    token_info=token_info,
                    status_code=403,
                    details={
                        "tenant_id": tenant.tenant_id,
                        "reason": err or "unauthorized",
                    },
                    request=request,
                )
                return web.json_response(
                    {
                        "ok": False,
                        "error": err or "Unauthorized",
                    },
                    status=403,
                )

            # Optional loopback check (match config_put behavior)
            import os

            allow_remote = (
                os.environ.get("OPENCLAW_ALLOW_REMOTE_ADMIN")
                or os.environ.get("MOLTBOT_ALLOW_REMOTE_ADMIN")
                or ""
            ).lower()
            if allow_remote not in ("1", "true", "yes", "on"):
                remote = request.remote or ""
                if not is_loopback_client(remote):
                    return web.json_response(
                        {
                            "ok": False,
                            "error": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
                        },
                        status=403,
                    )

            provider_override = (request.query.get("provider") or "").strip().lower()
            effective, _sources = get_effective_config(tenant_id=tenant.tenant_id)

            try:
                target = resolve_model_list_target(
                    provider_override,
                    effective,
                    tenant.tenant_id,
                )
            except ValueError as e:
                return web.json_response(
                    {"ok": False, "error": str(e)},
                    status=400,
                )
            except TypeError as e:
                return web.json_response(
                    {"ok": False, "error": str(e)},
                    status=400,
                )

            # R60: Check bounded TTL+LRU cache
            cached_entry = _cache_get(target.cache_key)
            if cached_entry:
                _ts, models = cached_entry
                if isinstance(models, list):
                    return web.json_response(
                        {
                            "ok": True,
                            "tenant_id": tenant.tenant_id,
                            "provider": target.provider,
                            "models": models,
                            "cached": True,
                        }
                    )

            # CRITICAL:
            # Local providers (e.g. ollama/lmstudio) intentionally work without API keys.
            # Do not change this gate back to `if not api_key`, or local model-list loading
            # will regress with false 400 errors.
            if target.requires_api_key and not target.api_key:
                return web.json_response(
                    {
                        "ok": False,
                        "error": f"No API key configured for provider '{target.provider}'.",
                    },
                    status=400,
                )

            # SSRF policy
            try:
                controls = get_llm_egress_controls(target.provider, target.base_url)
                validate_model_list_target(
                    target,
                    controls,
                    allow_insecure_base_url=_llm_insecure_override_enabled(),
                )
            except Exception as e:
                return web.json_response(
                    {"ok": False, "error": _format_llm_ssrf_error(e)},
                    status=403,
                )

            # Fetch /models
            try:
                try:
                    from ..services.safe_io import SSRFError
                except ImportError:
                    from services.safe_io import SSRFError  # type: ignore

                models = fetch_remote_model_list(
                    target,
                    controls,
                    pack_version=PACK_VERSION,
                    allow_insecure_base_url=_llm_insecure_override_enabled(),
                )

                return web.json_response(
                    {
                        "ok": True,
                        "tenant_id": tenant.tenant_id,
                        "provider": target.provider,
                        "models": models,
                        "cached": False,
                    }
                )
            except SSRFError as e:
                return web.json_response(
                    {"ok": False, "error": _format_llm_ssrf_error(e)},
                    status=403,
                )
            except RuntimeError as e:
                # safe_request_json raises RuntimeError for HTTP errors (non-200) contextually
                # check if it looks like an HTTP error
                str_e = str(e)
                if "HTTP" in str_e:
                    # Fallback: serve stale cache entry (if any) on fetch failure
                    stale = get_stale_cached_models(target.cache_key)
                    if stale:
                        _ts, models = stale
                        warning = f"Using cached list (refresh failed: {str_e})"
                        return web.json_response(
                            {
                                "ok": True,
                                "tenant_id": tenant.tenant_id,
                                "provider": target.provider,
                                "models": models,
                                "cached": True,
                                "warning": warning,
                            }
                        )
                    return web.json_response(
                        {"ok": False, "error": f"Upstream error: {str_e}"}, status=502
                    )
                raise e

            except Exception as e:
                stale = get_stale_cached_models(target.cache_key)
                if stale:
                    # IMPORTANT:
                    # Test path intentionally injects network failures to verify cache fallback.
                    # Keep this as warning (no traceback) to avoid noisy false-alarm logs.
                    logger.warning(
                        "Model list refresh failed, serving cached list: %s", e
                    )
                    _ts, models = stale
                    warning = f"Using cached list (refresh failed: {str(e)})"
                    return web.json_response(
                        {
                            "ok": True,
                            "tenant_id": tenant.tenant_id,
                            "provider": target.provider,
                            "models": models,
                            "cached": True,
                            "warning": warning,
                        }
                    )
                logger.exception("Failed to fetch model list")
                return web.json_response({"ok": False, "error": str(e)}, status=500)
    except TenantBoundaryError as e:
        return web.json_response(
            {"ok": False, "error": e.code, "message": str(e)},
            status=403,
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Update configuration",
    description="Updates non-secret LLM config.",
    audit="config.update",
    plane=RoutePlane.ADMIN,
)
async def config_put_handler(request: web.Request) -> web.Response:
    """
    PUT /moltbot/config
    Updates non-secret LLM config. Protected by admin boundary (S13) + CSRF (S26+).
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    # S26+: CSRF protection for convenience mode
    admin_token_configured = bool(get_admin_token())
    resp = require_same_origin_if_no_token(request, admin_token_configured)
    if resp:
        return resp

    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    # R99/S46: resolve identity context for non-repudiation audits.
    token_info = resolve_token_info(request)

    # Still enforce admin requirement (which checks hierarchy)
    allowed, err = require_admin_token(request)
    if not allowed:
        emit_audit_event(
            action="config.update",
            target="config.json",
            outcome="deny",
            token_info=token_info,
            status_code=403,
            details={"reason": err or "admin_token_required"},
            request=request,
        )
        return web.json_response(
            {
                "ok": False,
                "error": err or "Unauthorized",
            },
            status=403,
        )

    # S13: Optional loopback check
    import os

    allow_remote = (
        os.environ.get("OPENCLAW_ALLOW_REMOTE_ADMIN")
        or os.environ.get("MOLTBOT_ALLOW_REMOTE_ADMIN")
        or ""
    ).lower()
    if allow_remote not in ("1", "true", "yes", "on"):
        # Use S14 is_loopback which handles ipv6/mapped
        remote = get_client_ip(request)
        if not is_loopback(remote):
            emit_audit_event(
                action="config.update",
                target="config.json",
                outcome="deny",
                token_info=token_info,
                status_code=403,
                details={"reason": "remote_admin_denied", "remote": remote},
                request=request,
            )
            return web.json_response(
                {
                    "ok": False,
                    "error": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
                },
                status=403,
            )

    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            try:
                body = await request.json()
            except json.JSONDecodeError:
                return web.json_response(
                    {
                        "ok": False,
                        "error": "Invalid JSON body",
                    },
                    status=400,
                )

            # S66: Runtime guardrails are ENV-driven + runtime-only and must never be
            # persisted via config writes (prevents config drift / silent downgrade paths).
            if payload_contains_runtime_guardrails(body):
                emit_audit_event(
                    action="config.update",
                    target="config.json",
                    outcome="deny",
                    token_info=token_info,
                    status_code=400,
                    details={
                        "tenant_id": tenant.tenant_id,
                        "reason": "runtime_guardrails_runtime_only",
                        "code": CODE_RUNTIME_ONLY_PERSIST_FORBIDDEN,
                    },
                    request=request,
                )
                return web.json_response(
                    {
                        "ok": False,
                        "error": "runtime_guardrails are runtime-only (ENV-driven) and cannot be persisted via /config",
                        "code": CODE_RUNTIME_ONLY_PERSIST_FORBIDDEN,
                    },
                    status=400,
                )

            # Extract LLM config updates
            updates = body.get("llm", body)  # Support both { llm: {...} } and {...}
            if not isinstance(updates, dict):
                return web.json_response(
                    {
                        "ok": False,
                        "error": "Expected object with config fields",
                    },
                    status=400,
                )

            success, errors = update_config(updates, tenant_id=tenant.tenant_id)

            # R99: Standardized Audit Emission
            emit_audit_event(
                action="config.update",
                target="config.json",
                outcome="allow" if success else "error",
                token_info=token_info,
                status_code=200 if success else 400,
                details=(
                    {"tenant_id": tenant.tenant_id, "errors": errors}
                    if errors
                    else {"tenant_id": tenant.tenant_id}
                ),
                request=request,
            )

            if not success:
                return web.json_response(
                    {
                        "ok": False,
                        "errors": errors,
                    },
                    status=400,
                )

            # Return updated config
            effective, sources = get_effective_config(tenant_id=tenant.tenant_id)

            # R53: Calculate apply semantics
            apply_info = get_apply_semantics(list(updates.keys()))

            return web.json_response(
                {
                    "ok": True,
                    "tenant_id": tenant.tenant_id,
                    "config": effective,
                    "sources": sources,
                    "apply": apply_info,
                }
            )
    except TenantBoundaryError as e:
        emit_audit_event(
            action="config.update",
            target="config.json",
            outcome="deny",
            token_info=token_info,
            status_code=403,
            details={"reason": e.code},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": e.code, "message": str(e)},
            status=403,
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Test LLM connection",
    description="Tests LLM connection using provided or stored credentials.",
    audit="llm.test_connection",
    plane=RoutePlane.ADMIN,
)
async def llm_test_handler(request: web.Request) -> web.Response:
    """
    POST /moltbot/llm/test
    Tests LLM connection. Protected by admin boundary (S13) + CSRF (S26+).
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    try:
        from ..services.async_utils import run_in_thread
    except ImportError:
        from services.async_utils import run_in_thread
    try:
        # IMPORTANT: use package-relative import in ComfyUI runtime.
        # CRITICAL: Missing this import causes NameError in provider error handling.
        from ..services.provider_errors import ProviderHTTPError
    except ImportError:
        from services.provider_errors import ProviderHTTPError  # type: ignore

    # S26+: CSRF protection for convenience mode
    admin_token_configured = bool(get_admin_token())
    resp = require_same_origin_if_no_token(request, admin_token_configured)
    if resp:
        return resp

    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    token_info = resolve_token_info(request)

    # S13: Validate admin boundary
    allowed, err = require_admin_token(request)
    if not allowed:
        emit_audit_event(
            action="llm.test_connection",
            target="llm",
            outcome="deny",
            token_info=token_info,
            status_code=403,
            details={"reason": err or "unauthorized"},
            request=request,
        )
        return web.json_response(
            {
                "ok": False,
                "error": err or "Unauthorized",
            },
            status=403,
        )

    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            # IMPORTANT (Settings UX / provider mismatch):
            # - The Settings UI allows selecting provider/model/base_url without persisting config immediately.
            # - If this endpoint only uses effective config, "Test Connection" can misleadingly test the
            #   previous provider (often "openai") and report: "API key not configured for provider 'openai'"
            #   even when the UI is set to Gemini and a Gemini key is stored.
            # Therefore, accept optional overrides in the JSON body.
            #
            # Contract:
            # - Empty body -> test effective config
            # - Body may include: provider, model, base_url, timeout_sec, max_retries
            try:
                body = await request.json()
                if body is None:
                    body = {}
            except Exception:
                body = {}

            if body and not isinstance(body, dict):
                return web.json_response(
                    {"ok": False, "error": "Expected JSON object body (or empty body)"},
                    status=400,
                )

            provider = (
                body.get("provider") if isinstance(body.get("provider"), str) else None
            )
            model = body.get("model") if isinstance(body.get("model"), str) else None
            base_url = (
                body.get("base_url") if isinstance(body.get("base_url"), str) else None
            )

            timeout_val = body.get("timeout_sec")
            timeout_sec = None
            if (
                isinstance(timeout_val, (int, float, str))
                and str(timeout_val).strip() != ""
            ):
                try:
                    timeout_sec = int(timeout_val)
                except Exception:
                    return web.json_response(
                        {"ok": False, "error": "timeout_sec must be an integer"},
                        status=400,
                    )

            retries_val = body.get("max_retries")
            max_retries = None
            if (
                isinstance(retries_val, (int, float, str))
                and str(retries_val).strip() != ""
            ):
                try:
                    max_retries = int(retries_val)
                except Exception:
                    return web.json_response(
                        {"ok": False, "error": "max_retries must be an integer"},
                        status=400,
                    )

            # Initialize client (uses effective config by default; overrides if provided)
            client = LLMClient(
                provider=provider,
                base_url=base_url,
                model=model,
                timeout=timeout_sec,
                max_retries=max_retries,
            )

            # Run test in a worker thread since LLMClient is sync
            result = await run_in_thread(
                client.complete,
                system="You are a test assistant.",
                user_message="Respond with exactly: OK",
                max_tokens=10,
            )

            # Check result
            if result and "text" in result:
                emit_audit_event(
                    action="llm.test_connection",
                    target=f"{client.provider}:{client.model}",
                    outcome="allow",
                    token_info=token_info,
                    status_code=200,
                    details={
                        "tenant_id": tenant.tenant_id,
                        "provider": client.provider,
                        "model": client.model,
                    },
                    request=request,
                )
                return web.json_response(
                    {
                        "ok": True,
                        "tenant_id": tenant.tenant_id,
                        "message": "Connection successful",
                        "response": result["text"].strip(),
                        "provider": client.provider,
                        "model": client.model,
                    }
                )

            emit_audit_event(
                action="llm.test_connection",
                target=f"{client.provider}:{client.model}",
                outcome="error",
                token_info=token_info,
                status_code=500,
                details={
                    "tenant_id": tenant.tenant_id,
                    "provider": client.provider,
                    "model": client.model,
                    "error": "Empty response",
                },
                request=request,
            )
            return web.json_response(
                {
                    "ok": False,
                    "error": "Empty or invalid response from LLM",
                }
            )
    except TenantBoundaryError as e:
        emit_audit_event(
            action="llm.test_connection",
            target="llm",
            outcome="deny",
            token_info=token_info,
            status_code=403,
            details={"reason": e.code},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": e.code, "message": str(e)},
            status=403,
        )
    except Exception as e:
        logger.exception("LLM test failed")
        emit_audit_event(
            action="llm.test_connection",
            target="llm",
            outcome="error",
            token_info=token_info,
            status_code=500,
            details={"error": str(e)},
            request=request,
        )
        return web.json_response(
            {
                "ok": False,
                "error": str(e),
            },
            status=500,
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,  # Consumed by connector, costs money/tokens
    summary="Chat completion",
    description="Run a simple chat completion using server-side LLM config.",
    audit="llm.chat_completion",
    plane=RoutePlane.ADMIN,
)
async def llm_chat_handler(request: web.Request) -> web.Response:
    """
    POST /openclaw/llm/chat (legacy: /moltbot/llm/chat)
    Run a simple chat completion using server-side LLM config + keys.
    This endpoint is intended for the connector; no prompt content is logged.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    try:
        from ..services.async_utils import run_in_thread
    except ImportError:
        from services.async_utils import run_in_thread
    try:
        # IMPORTANT: use package-relative import in ComfyUI runtime.
        # CRITICAL: Missing this import causes NameError in provider error handling.
        from ..services.provider_errors import ProviderHTTPError
    except ImportError:
        from services.provider_errors import ProviderHTTPError  # type: ignore

    # S28: CSRF protection for convenience mode (no admin token configured)
    admin_token_configured = bool(get_admin_token())
    resp = require_same_origin_if_no_token(request, admin_token_configured)
    if resp:
        return resp

    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    # NOTE: Keep this server-side. Connector cannot access UI-stored secrets directly.
    # This endpoint ensures keys are resolved via backend config + secret store.
    # S13: Validate admin boundary (or loopback if no admin token configured)
    token_info = resolve_token_info(request)
    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {
                "ok": False,
                "error": err or "Unauthorized",
            },
            status=403,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "error": "Expected JSON object body"},
            status=400,
        )

    system = body.get("system") if isinstance(body.get("system"), str) else ""
    user_message = (
        body.get("user_message")
        if isinstance(body.get("user_message"), str)
        else body.get("message") if isinstance(body.get("message"), str) else ""
    )
    temperature = (
        body.get("temperature")
        if isinstance(body.get("temperature"), (int, float))
        else 0.7
    )
    max_tokens = (
        body.get("max_tokens") if isinstance(body.get("max_tokens"), int) else 1024
    )

    if not user_message:
        return web.json_response(
            {"ok": False, "error": "missing_user_message"},
            status=400,
        )

    # S29: Debug-level structured log — metadata only, never raw prompt content.
    logger.debug(
        "llm_chat: has_system=%s msg_len=%d temperature=%.2f max_tokens=%d",
        bool(system),
        len(user_message),
        temperature,
        max_tokens,
    )

    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            client = LLMClient()

            def _run():
                return client.complete(
                    system=system,
                    user_message=user_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

            result = await run_in_thread(_run)
            text = ""
            if isinstance(result, dict):
                text = result.get("text") or ""
            return web.json_response(
                {"ok": True, "tenant_id": tenant.tenant_id, "text": text}
            )
    except TenantBoundaryError as e:
        return web.json_response(
            {"ok": False, "error": e.code, "message": str(e)},
            status=403,
        )
    except ValueError as e:
        # Common: missing API key for selected provider
        return web.json_response(
            {"ok": False, "error": str(e)},
            status=400,
        )
    except ProviderHTTPError as e:
        # IMPORTANT (recurring support issue):
        # Do not swallow provider errors into a generic "llm_request_failed" without context.
        # The connector can safely surface *redacted* provider messages (no prompt content)
        # so users can fix misconfiguration (401/403/429, SSRF allowlist, etc.) quickly.
        payload = {
            "ok": False,
            "error": f"{e.provider} HTTP {e.status_code}: {e.message}",
            "provider": e.provider,
            "status_code": e.status_code,
        }
        if getattr(e, "retry_after", None):
            payload["retry_after"] = e.retry_after
        return web.json_response(payload, status=e.status_code)
    except Exception as e:
        # S29: Redact exception message to prevent accidental prompt content leakage.
        # Downgraded from error → warning (non-actionable for operators when provider-specific).
        try:
            from services.redaction import redact_text  # type: ignore
        except ImportError:
            try:
                from ..services.redaction import redact_text
            except ImportError:
                redact_text = str  # type: ignore
        logger.warning(
            "LLM chat request failed: %s: %s",
            type(e).__name__,
            redact_text(str(e)),
        )
        return web.json_response(
            {"ok": False, "error": "llm_request_failed"},
            status=500,
        )
