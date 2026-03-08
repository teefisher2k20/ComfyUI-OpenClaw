"""
API routes for observability endpoints.
Registers /openclaw/* endpoints (and legacy /moltbot/*) against ComfyUI PromptServer.
"""

# IMPORTANT: __future__ imports MUST be the first non-docstring line in the file.
# Do not move this import or insert code above it, or ComfyUI route registration will fail.
from __future__ import annotations

import json
import os
import sys
import time

if __package__ and "." in __package__:
    from ..services.import_fallback import import_attrs_dual
else:
    from services.import_fallback import import_attrs_dual  # type: ignore

# R98 / R64: Endpoint Metadata import via shared helper
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

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover (optional for unit tests)
    web = None  # type: ignore

PACK_NAME = PACK_VERSION = PACK_START_TIME = LOG_FILE = get_api_key = None  # type: ignore
metrics = tail_log = require_observability_access = check_rate_limit = trace_store = None  # type: ignore
get_executor_diagnostics = None  # type: ignore
webhook_handler = webhook_submit_handler = webhook_validate_handler = capabilities_handler = preflight_handler = None  # type: ignore
config_get_handler = config_put_handler = llm_test_handler = llm_models_handler = llm_chat_handler = None  # type: ignore
remote_admin_page_handler = None  # type: ignore  # F61
security_doctor_handler = None  # type: ignore  # S30
connector_installations_list_handler = connector_installation_get_handler = None  # type: ignore
connector_installation_resolve_handler = connector_installation_audit_handler = None  # type: ignore
templates_list_handler = None  # type: ignore
rewrite_recipes_list_handler = rewrite_recipe_get_handler = None  # type: ignore
rewrite_recipe_create_handler = rewrite_recipe_update_handler = None  # type: ignore
rewrite_recipe_delete_handler = rewrite_recipe_dry_run_handler = None  # type: ignore
rewrite_recipe_apply_handler = None  # type: ignore
model_search_handler = model_download_create_handler = None  # type: ignore
model_download_list_handler = model_download_get_handler = None  # type: ignore
model_download_cancel_handler = model_import_handler = None  # type: ignore
model_installations_list_handler = None  # type: ignore
secrets_status_handler = secrets_put_handler = secrets_delete_handler = None  # type: ignore
list_checkpoints_handler = create_checkpoint_handler = get_checkpoint_handler = delete_checkpoint_handler = None  # type: ignore
events_stream_handler = events_poll_handler = None  # type: ignore  # R71
redact_text = None  # type: ignore

if web is not None:
    # Import discipline:
    # - ComfyUI runtime: package-relative imports only (prevents collisions with other custom nodes).
    # - Unit tests: allow top-level imports.
    (capabilities_handler,) = import_attrs_dual(
        __package__,
        "..api.capabilities",
        "api.capabilities",
        ("capabilities_handler",),
    )
    (
        connector_installations_list_handler,
        connector_installation_get_handler,
        connector_installation_resolve_handler,
        connector_installation_audit_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.connector_contracts",
        "api.connector_contracts",
        (
            "connector_installations_list_handler",
            "connector_installation_get_handler",
            "connector_installation_resolve_handler",
            "connector_installation_audit_handler",
        ),
    )
    (
        create_checkpoint_handler,
        delete_checkpoint_handler,
        get_checkpoint_handler,
        list_checkpoints_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.checkpoints_handler",
        "api.checkpoints_handler",
        (
            "create_checkpoint_handler",
            "delete_checkpoint_handler",
            "get_checkpoint_handler",
            "list_checkpoints_handler",
        ),
    )
    (
        config_get_handler,
        config_put_handler,
        llm_chat_handler,
        llm_models_handler,
        llm_test_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.config",
        "api.config",
        (
            "config_get_handler",
            "config_put_handler",
            "llm_chat_handler",
            "llm_models_handler",
            "llm_test_handler",
        ),
    )
    (events_poll_handler, events_stream_handler) = import_attrs_dual(  # R71
        __package__,
        "..api.events",
        "api.events",
        ("events_poll_handler", "events_stream_handler"),
    )
    (remote_admin_page_handler,) = import_attrs_dual(  # F61
        __package__,
        "..api.remote_admin",
        "api.remote_admin",
        ("remote_admin_page_handler",),
    )
    (inventory_handler, preflight_handler) = import_attrs_dual(
        __package__,
        "..api.preflight_handler",
        "api.preflight_handler",
        ("inventory_handler", "preflight_handler"),
    )
    (secrets_delete_handler, secrets_put_handler, secrets_status_handler) = (
        import_attrs_dual(
            __package__,
            "..api.secrets",
            "api.secrets",
            ("secrets_delete_handler", "secrets_put_handler", "secrets_status_handler"),
        )
    )
    (security_doctor_handler,) = import_attrs_dual(  # S30
        __package__,
        "..api.security_doctor",
        "api.security_doctor",
        ("security_doctor_handler",),
    )
    (templates_list_handler,) = import_attrs_dual(
        __package__,
        "..api.templates",
        "api.templates",
        ("templates_list_handler",),
    )
    (
        rewrite_recipe_apply_handler,
        rewrite_recipe_create_handler,
        rewrite_recipe_delete_handler,
        rewrite_recipe_dry_run_handler,
        rewrite_recipe_get_handler,
        rewrite_recipe_update_handler,
        rewrite_recipes_list_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.rewrite_recipes",
        "api.rewrite_recipes",
        (
            "rewrite_recipe_apply_handler",
            "rewrite_recipe_create_handler",
            "rewrite_recipe_delete_handler",
            "rewrite_recipe_dry_run_handler",
            "rewrite_recipe_get_handler",
            "rewrite_recipe_update_handler",
            "rewrite_recipes_list_handler",
        ),
    )
    (
        model_download_cancel_handler,
        model_download_create_handler,
        model_download_get_handler,
        model_download_list_handler,
        model_import_handler,
        model_installations_list_handler,
        model_search_handler,
    ) = import_attrs_dual(
        __package__,
        "..api.model_manager",
        "api.model_manager",
        (
            "model_download_cancel_handler",
            "model_download_create_handler",
            "model_download_get_handler",
            "model_download_list_handler",
            "model_import_handler",
            "model_installations_list_handler",
            "model_search_handler",
        ),
    )
    (tools_list_handler, tools_run_handler) = import_attrs_dual(  # S12
        __package__,
        "..api.tools",
        "api.tools",
        ("tools_list_handler", "tools_run_handler"),
    )
    (webhook_handler,) = import_attrs_dual(
        __package__,
        "..api.webhook",
        "api.webhook",
        ("webhook_handler",),
    )
    (webhook_submit_handler,) = import_attrs_dual(
        __package__,
        "..api.webhook_submit",
        "api.webhook_submit",
        ("webhook_submit_handler",),
    )
    (webhook_validate_handler,) = import_attrs_dual(
        __package__,
        "..api.webhook_validate",
        "api.webhook_validate",
        ("webhook_validate_handler",),
    )

    # IMPORTANT: use PACK_VERSION / PACK_START_TIME from config.
    # Do NOT import VERSION or config_path (they do not exist) or route registration will fail.
    (LOG_FILE, PACK_NAME, PACK_START_TIME, PACK_VERSION) = import_attrs_dual(
        __package__,
        "..config",
        "config",
        ("LOG_FILE", "PACK_NAME", "PACK_START_TIME", "PACK_VERSION"),
    )

    # CRITICAL: These imports MUST remain present.
    # If edited out, module-level placeholders stay as None and handlers raise at runtime
    # (e.g., TypeError: 'NoneType' object is not callable), producing noisy aiohttp tracebacks.
    (require_admin_token, require_observability_access) = import_attrs_dual(
        __package__,
        "..services.access_control",
        "services.access_control",
        ("require_admin_token", "require_observability_access"),
    )
    (tail_log,) = import_attrs_dual(
        __package__,
        "..services.log_tail",
        "services.log_tail",
        ("tail_log",),
    )
    (metrics,) = import_attrs_dual(
        __package__,
        "..services.metrics",
        "services.metrics",
        ("metrics",),
    )
    (get_executor_diagnostics,) = import_attrs_dual(
        __package__,
        "..services.async_utils",
        "services.async_utils",
        ("get_executor_diagnostics",),
    )
    (
        create_compare_handler,
        create_sweep_handler,
        get_experiment_handler,
        list_experiments_handler,
        select_apply_winner_handler,
        update_experiment_handler,
    ) = import_attrs_dual(
        __package__,
        "..services.parameter_lab",
        "services.parameter_lab",
        (
            "create_compare_handler",
            "create_sweep_handler",
            "get_experiment_handler",
            "list_experiments_handler",
            "select_apply_winner_handler",
            "update_experiment_handler",
        ),
    )
    (check_rate_limit,) = import_attrs_dual(
        __package__,
        "..services.rate_limit",
        "services.rate_limit",
        ("check_rate_limit",),
    )
    (redact_text,) = import_attrs_dual(
        __package__,
        "..services.redaction",
        "services.redaction",
        ("redact_text",),
    )

    # IMPORTANT: services.trace does NOT expose a `trace` symbol.
    # Do not import `trace` here or route registration will fail.
    (trace_store,) = import_attrs_dual(
        __package__,
        "..services.trace_store",
        "services.trace_store",
        ("trace_store",),
    )


def check_dependency(module_name: str) -> bool:
    """Check if a module is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _ensure_observability_deps_ready() -> tuple[bool, str | None]:
    """
    Defensive guard against a recurring class of regressions:
    if the import block above is edited incorrectly, the module-level
    placeholders stay as None and handlers raise TypeError at runtime.
    """
    missing: list[str] = []
    if not callable(require_observability_access):
        missing.append("require_observability_access")
    if not callable(check_rate_limit):
        missing.append("check_rate_limit")
    if not callable(tail_log):
        missing.append("tail_log")
    if missing:
        return (
            False,
            "Backend not fully initialized (missing route dependencies: "
            + ", ".join(missing)
            + ").",
        )
    return True, None


@endpoint_metadata(
    auth=AuthTier.PUBLIC,
    risk=RiskTier.LOW,
    summary="Health check",
    description="Returns pack status, uptime, dependencies, and stats.",
    audit="health.check",
    plane=RoutePlane.USER,
)
async def health_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/health (legacy: /moltbot/health)
    Returns pack status, uptime, dependencies, config presence, and stats.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    try:
        from ..services.llm_client import LLMClient
        from ..services.providers.keys import requires_api_key
    except ImportError:
        from services.llm_client import LLMClient
        from services.providers.keys import requires_api_key

    uptime = time.time() - PACK_START_TIME

    # Get provider info from LLMClient
    provider_info = {
        "provider": "unknown",
        "key_configured": False,
        "model": "unknown",
        "base_url": None,
        "api_type": None,
    }
    key_required = True
    try:
        client = LLMClient()
        provider_info = client.get_provider_summary()
        key_required = requires_api_key(provider_info.get("provider", "unknown"))
    except Exception:
        provider_info = {
            "provider": "unknown",
            "key_configured": False,
            "model": "unknown",
            "base_url": None,
            "api_type": None,
        }
        key_required = True

    # S15: Access Policy Info
    try:
        from ..services.access_control import is_loopback

        token_val = (
            os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN")
            or os.environ.get("MOLTBOT_OBSERVABILITY_TOKEN")
            or ""
        ).strip()
        token_configured = bool(token_val)
    except ImportError:
        from services.access_control import is_loopback

        token_val = (
            os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN")
            or os.environ.get("MOLTBOT_OBSERVABILITY_TOKEN")
            or ""
        ).strip()
        token_configured = bool(token_val)

    # Determine basic policy state
    policy_mode = "token" if token_configured else "loopback_only"

    # Metrics snapshot
    # Metrics snapshot (robust even if metrics implementation changes)
    try:
        m_snapshot = metrics.get_snapshot()
    except Exception:
        m_snapshot = {"errors_captured": 0, "logs_processed": 0}
    try:
        executor_snapshot = get_executor_diagnostics() or {}
    except Exception:
        executor_snapshot = {}

    # Job Event Store Stats (Backpressure)
    job_stats = {}
    try:
        from ..services.job_events import get_job_event_store

        store = get_job_event_store()
        job_stats = store.stats()
    except Exception:
        pass

    # H3 (F55): Include control_plane info for frontend mode badge
    cp_info = {}
    runtime_prof = "minimal"
    try:
        try:
            from ..services.capabilities import _get_control_plane_info
            from ..services.runtime_profile import get_runtime_profile
        except ImportError:
            from services.capabilities import _get_control_plane_info
            from services.runtime_profile import get_runtime_profile
        cp_info = _get_control_plane_info()
        runtime_prof = get_runtime_profile().value
    except Exception:
        pass

    return web.json_response(
        {
            "ok": True,
            "pack": {
                "name": PACK_NAME,
                "version": PACK_VERSION,
                "dependencies": {
                    "aiohttp": check_dependency("aiohttp"),
                    "watchdog": check_dependency("watchdog"),
                },
            },
            "uptime_sec": uptime,
            "config": {
                "provider": provider_info.get("provider"),
                "model": provider_info.get("model"),
                "base_url": provider_info.get("base_url"),
                "api_type": provider_info.get("api_type"),
                "llm_key_configured": provider_info.get("key_configured", False),
                "llm_key_required": key_required,
            },
            "stats": {
                "errors_captured": m_snapshot["errors_captured"],
                "logs_processed": m_snapshot["logs_processed"],
                "executors": executor_snapshot,  # R129
                "observability": job_stats,  # R87
            },
            # S15: Exposure Detection
            "access_policy": {
                "observability": policy_mode,
                "token_configured": token_configured,
            },
            # H3 (F55): Control plane mode for frontend badge
            "control_plane": cp_info,
            "runtime_profile": runtime_prof,
        }
    )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Tail logs",
    description="Returns the last N lines of the log file.",
    audit="logs.tail",
    plane=RoutePlane.ADMIN,
)
async def logs_tail_handler(request: web.Request) -> web.Response:
    """GET /moltbot/logs/tail - Returns the last N lines of the log file."""
    if web is None:
        raise RuntimeError("aiohttp not available")
    ok, init_error = _ensure_observability_deps_ready()
    if not ok:
        return web.json_response({"ok": False, "error": init_error}, status=500)
    # S34: Trace/Log data is high sensitivity -> Require Admin Token
    allowed, error = require_admin_token(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    # S17: Rate Limit
    if not check_rate_limit(request, "logs"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"},
            status=429,
            headers={"Retry-After": "60"},
        )

    try:
        # Default 50 lines, max 500
        # Support both 'n' (internal preference) and 'lines' (legacy frontend)
        line_count = 50

        val_n = request.query.get("n")
        val_lines = request.query.get("lines")

        target_val = val_n if val_n is not None else val_lines

        if target_val:
            try:
                line_count = int(target_val)
            except ValueError:
                pass

        # Cap at 500
        line_count = min(max(line_count, 1), 500)

        # R31: Filter parameters
        trace_id_filter = request.query.get("trace_id")
        prompt_id_filter = request.query.get("prompt_id")

        content = tail_log(LOG_FILE, line_count)

        # R31: Apply filtering if requested
        if trace_id_filter or prompt_id_filter:
            filtered_content = []
            for line in content:
                # Simple substring match (case-sensitive for IDs)
                if trace_id_filter and trace_id_filter in line:
                    filtered_content.append(line)
                elif prompt_id_filter and prompt_id_filter in line:
                    filtered_content.append(line)
            content = filtered_content

        # S24: Apply redaction to each line
        if redact_text:
            content = [redact_text(line) for line in content]

        # R31: Enforce max bytes limit (100KB total)
        MAX_BYTES = 100_000
        total_bytes = sum(len(line.encode("utf-8")) for line in content)
        if total_bytes > MAX_BYTES:
            # Truncate from end to stay under limit
            truncated = []
            current_bytes = 0
            for line in reversed(content):
                line_bytes = len(line.encode("utf-8"))
                if current_bytes + line_bytes > MAX_BYTES:
                    break
                truncated.insert(0, line)
                current_bytes += line_bytes
            content = truncated

        return web.json_response(
            {
                "ok": True,
                "content": content,
                "filtered": bool(trace_id_filter or prompt_id_filter),
            }
        )
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="List jobs",
    description="Stub endpoint for job listing.",
    audit="jobs.list",
    plane=RoutePlane.ADMIN,
)
async def jobs_handler(request: web.Request) -> web.Response:
    """
    GET /moltbot/jobs
    Stub endpoint for job listing (not implemented yet).
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    return web.json_response(
        {
            "ok": True,
            "jobs": [],
            "not_implemented": True,
            "message": "Job persistence is not yet implemented. This is a stub endpoint.",
        }
    )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Get trace",
    description="Returns redacted timeline for a prompt.",
    audit="trace.get",
    plane=RoutePlane.ADMIN,
)
async def trace_handler(request: web.Request) -> web.Response:
    """GET /moltbot/trace/{prompt_id} - Returns trace_id and redacted timeline."""
    if web is None:
        raise RuntimeError("aiohttp not available")
    ok, init_error = _ensure_observability_deps_ready()
    if not ok:
        return web.json_response({"ok": False, "error": init_error}, status=500)
    # S34: Trace/Log data is high sensitivity -> Require Admin Token
    allowed, error = require_admin_token(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    prompt_id = request.match_info.get("prompt_id")
    if not prompt_id:
        return web.json_response(
            {"ok": False, "error": "missing_prompt_id"}, status=400
        )

    rec = trace_store.get(prompt_id)
    if not rec:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)

    # S24: Apply redaction to trace data
    trace_data = rec.to_dict()
    try:
        from ..services.redaction import redact_json
    except ImportError:
        from services.redaction import redact_json

    if redact_json:
        trace_data = redact_json(trace_data)

    return web.json_response({"ok": True, "trace": trace_data})


assist = None
if web is not None:
    # Initialize Assist Handlers
    try:
        from ..api.assist import AssistHandlers
    except ImportError:
        from api.assist import AssistHandlers
    assist = AssistHandlers()


def register_dual_route(server, method: str, path: str, handler) -> None:
    """
    Registers a route to both the standard PromptServer table
    and directly to the aiohttp router with and without /api prefix
    to ensure robustness against loading order (R26/F24).
    """
    # IMPORTANT: handler MUST be callable. If imports fail, handlers remain None.
    # Registering a None handler crashes ComfyUI at startup (aiohttp assertion).
    if not callable(handler):
        print(
            f"[OpenClaw] Warning: Skipping route {method} {path} because handler is missing (None)."
        )
        return
    # Phase 3 Deprecation wrapper for legacy paths
    actual_handler = handler
    if path.startswith("/moltbot"):
        from functools import wraps

        @wraps(handler)
        async def _deprecated_handler(request: web.Request) -> web.Response:
            try:
                # Assuming `metrics` is available in scope (from module level imports)
                if metrics:
                    metrics.inc("legacy_api_hits")
            except Exception:
                pass
            print(
                f"[OpenClaw] DEPRECATION WARNING: Legacy route accessed: {request.path}. Please migrate to /openclaw/* equivalents."
            )
            return await handler(request)

        actual_handler = _deprecated_handler

    # 1. Standard ComfyUI registration
    if method == "GET":
        server.routes.get(path)(actual_handler)
    elif method == "POST":
        server.routes.post(path)(actual_handler)
    elif method == "PUT":
        server.routes.put(path)(actual_handler)
    elif method == "DELETE":
        server.routes.delete(path)(actual_handler)

    # 2. Hardened direct registration
    if hasattr(server, "app") and hasattr(server.app, "router"):
        # We try to register /api/... and legacy /... explicitly
        # This fixes 404s if the extension loads after ComfyUI has compiled routes
        targets = [path, "/api" + path]
        for t in targets:
            try:
                server.app.router.add_route(method, t, handler)
            except RuntimeError:
                # Route likely exists (e.g. added by step 1 or duplicate)
                pass
            except Exception as e:
                print(f"[OpenClaw] Warning: Failed to register fallback route {t}: {e}")


def _is_openclaw_managed_path(path: str) -> bool:
    if not isinstance(path, str):
        return False
    return (
        path.startswith("/openclaw")
        or path.startswith("/moltbot")
        or path.startswith("/api/openclaw")
        or path.startswith("/api/moltbot")
        or path.startswith("/bridge")
        or path.startswith("/api/bridge")
    )


def _resolve_mae_profile() -> str:
    profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local").strip().lower()
    if profile in {"public", "hardened"}:
        return profile

    try:
        if __package__ and "." in __package__:
            from ..services.runtime_profile import get_runtime_profile
        else:
            from services.runtime_profile import get_runtime_profile
        runtime_profile = get_runtime_profile().value
        if runtime_profile == "hardened":
            return "hardened"
    except Exception:
        pass
    return profile or "local"


def _run_mae_startup_gate(server) -> None:
    if not hasattr(server, "app"):
        return

    try:
        if __package__ and "." in __package__:
            from ..services.endpoint_manifest import (
                generate_manifest,
                validate_mae_posture,
            )
        else:
            from services.endpoint_manifest import (
                generate_manifest,
                validate_mae_posture,
            )
    except Exception as e:
        print(f"[OpenClaw] Warning: S60 MAE gate unavailable: {e}")
        return

    mae_profile = _resolve_mae_profile()
    manifest = generate_manifest(server.app)
    scoped_manifest = [
        entry for entry in manifest if _is_openclaw_managed_path(entry.get("path", ""))
    ]
    ok, violations = validate_mae_posture(scoped_manifest, profile=mae_profile)
    if ok:
        return

    message = "S60 MAE posture validation failed:\n" + "\n".join(
        f"- {item}" for item in violations
    )
    if mae_profile in {"public", "hardened"}:
        raise RuntimeError(message)
    print(f"[OpenClaw] Warning: {message}")


def register_routes(server) -> None:
    """
    Register API routes with the ComfyUI server.
    Called from __init__.py during pack initialization.
    """
    # S56: Startup deployment profile gate (fail-closed pre-route validation).
    # Must run BEFORE any route or worker registration.
    try:
        try:
            from ..services.startup_profile_gate import enforce_startup_gate
        except (ImportError, ValueError):
            from services.startup_profile_gate import enforce_startup_gate

        enforce_startup_gate()
    except RuntimeError:
        # CRITICAL: fail-closed. Never continue route registration after S56
        # startup gate failure.
        raise

    print("[OpenClaw] Registering routes (Shim Alignment R26)...")
    prefixes = ["/openclaw", "/moltbot"]  # new, legacy

    # Core Observability & Config
    for prefix in prefixes:
        core_routes = [
            ("GET", f"{prefix}/admin", remote_admin_page_handler),  # F61
            ("GET", f"{prefix}/health", health_handler),
            ("GET", f"{prefix}/logs/tail", logs_tail_handler),
            ("GET", f"{prefix}/jobs", jobs_handler),
            ("GET", f"{prefix}/trace/{{prompt_id}}", trace_handler),
            ("POST", f"{prefix}/webhook", webhook_handler),
            ("POST", f"{prefix}/webhook/submit", webhook_submit_handler),
            (
                "POST",
                f"{prefix}/webhook/validate",
                webhook_validate_handler,
            ),  # R32: Validation endpoint
            ("GET", f"{prefix}/capabilities", capabilities_handler),
            ("GET", f"{prefix}/config", config_get_handler),
            ("PUT", f"{prefix}/config", config_put_handler),
            ("POST", f"{prefix}/llm/test", llm_test_handler),
            # NOTE: Connector uses this endpoint to avoid missing UI-stored keys.
            ("POST", f"{prefix}/llm/chat", llm_chat_handler),
            (
                "GET",
                f"{prefix}/llm/models",
                llm_models_handler,
            ),  # F20+: Remote model list (best-effort)
            (
                "GET",
                f"{prefix}/templates",
                templates_list_handler,
            ),  # F29: Template quick list for chat connectors
            (
                "POST",
                f"{prefix}/preflight",
                preflight_handler,
            ),  # R42: Preflight diagnostics
            (
                "GET",
                f"{prefix}/preflight/inventory",
                inventory_handler,
            ),  # F28: Explorer Inventory
            (
                "GET",
                f"{prefix}/checkpoints",
                list_checkpoints_handler,
            ),  # R47: Checkpoints
            (
                "POST",
                f"{prefix}/checkpoints",
                create_checkpoint_handler,
            ),
            (
                "GET",
                f"{prefix}/checkpoints/{{id}}",
                get_checkpoint_handler,
            ),
            (
                "DELETE",
                f"{prefix}/checkpoints/{{id}}",
                delete_checkpoint_handler,
            ),
            ("GET", f"{prefix}/rewrite/recipes", rewrite_recipes_list_handler),
            ("POST", f"{prefix}/rewrite/recipes", rewrite_recipe_create_handler),
            (
                "GET",
                f"{prefix}/rewrite/recipes/{{recipe_id}}",
                rewrite_recipe_get_handler,
            ),
            (
                "PUT",
                f"{prefix}/rewrite/recipes/{{recipe_id}}",
                rewrite_recipe_update_handler,
            ),
            (
                "DELETE",
                f"{prefix}/rewrite/recipes/{{recipe_id}}",
                rewrite_recipe_delete_handler,
            ),
            (
                "POST",
                f"{prefix}/rewrite/recipes/{{recipe_id}}/dry-run",
                rewrite_recipe_dry_run_handler,
            ),
            (
                "POST",
                f"{prefix}/rewrite/recipes/{{recipe_id}}/apply",
                rewrite_recipe_apply_handler,
            ),
            ("GET", f"{prefix}/models/search", model_search_handler),
            ("POST", f"{prefix}/models/downloads", model_download_create_handler),
            ("GET", f"{prefix}/models/downloads", model_download_list_handler),
            (
                "GET",
                f"{prefix}/models/downloads/{{task_id}}",
                model_download_get_handler,
            ),
            (
                "POST",
                f"{prefix}/models/downloads/{{task_id}}/cancel",
                model_download_cancel_handler,
            ),
            ("POST", f"{prefix}/models/import", model_import_handler),
            (
                "GET",
                f"{prefix}/models/installations",
                model_installations_list_handler,
            ),
            (
                "GET",
                f"{prefix}/secrets/status",
                secrets_status_handler,
            ),  # S25: Secret status (no values)
            ("PUT", f"{prefix}/secrets", secrets_put_handler),  # S25: Save secret
            (
                "GET",
                f"{prefix}/events/stream",
                events_stream_handler,
            ),  # R71: SSE event stream
            (
                "GET",
                f"{prefix}/events",
                events_poll_handler,
            ),  # R71: JSON polling fallback
            (
                "DELETE",
                f"{prefix}/secrets/{{provider}}",
                secrets_delete_handler,
            ),  # S25: Clear secret
            (
                "GET",
                f"{prefix}/security/doctor",
                security_doctor_handler,
            ),  # S30: Security Doctor diagnostics
            (
                "GET",
                f"{prefix}/tools",
                tools_list_handler,
            ),  # S12: List allowed tools
            (
                "POST",
                f"{prefix}/tools/{{name}}/run",
                tools_run_handler,
            ),  # S12: Execute tool (admin only)
            # F52: Parameter Lab
            ("POST", f"{prefix}/lab/sweep", create_sweep_handler),
            ("POST", f"{prefix}/lab/compare", create_compare_handler),
            ("GET", f"{prefix}/lab/experiments", list_experiments_handler),
            ("GET", f"{prefix}/lab/experiments/{{exp_id}}", get_experiment_handler),
            (
                "POST",
                f"{prefix}/lab/experiments/{{exp_id}}/runs/{{run_id}}",
                update_experiment_handler,
            ),
            (
                "POST",
                f"{prefix}/lab/experiments/{{exp_id}}/winner",
                select_apply_winner_handler,
            ),
        ]

        for method, path, handler in core_routes:
            register_dual_route(server, method, path, handler)

    # F8/F21 Assist Routes
    # R84 Boot Boundary: CORE (Planner/Refiner part of core/assist)
    if assist:
        for prefix in prefixes:
            register_dual_route(
                server,
                "GET",
                f"{prefix}/assist/planner/profiles",
                assist.planner_profiles_handler,
            )
            register_dual_route(
                server, "POST", f"{prefix}/assist/planner", assist.planner_handler
            )
            register_dual_route(
                server,
                "POST",
                f"{prefix}/assist/planner/stream",
                assist.planner_stream_handler,
            )
            register_dual_route(
                server, "POST", f"{prefix}/assist/refiner", assist.refiner_handler
            )
            register_dual_route(
                server,
                "POST",
                f"{prefix}/assist/refiner/stream",
                assist.refiner_stream_handler,
            )
            register_dual_route(
                server,
                "POST",
                f"{prefix}/assist/automation/compose",
                assist.compose_handler,
            )

    # R126: Connector installation diagnostics/read APIs
    if connector_installations_list_handler:
        for prefix in prefixes:
            register_dual_route(
                server,
                "GET",
                f"{prefix}/connector/installations",
                connector_installations_list_handler,
            )
            register_dual_route(
                server,
                "GET",
                f"{prefix}/connector/installations/resolve",
                connector_installation_resolve_handler,
            )
            register_dual_route(
                server,
                "GET",
                f"{prefix}/connector/installations/audit",
                connector_installation_audit_handler,
            )
            register_dual_route(
                server,
                "GET",
                f"{prefix}/connector/installations/{{installation_id}}",
                connector_installation_get_handler,
            )

    # F10 Bridge Routes (Sidecar)
    # R84 Boot Boundary: BRIDGE
    # F10 Bridge Routes (Sidecar)
    # R84 Boot Boundary: BRIDGE
    try:
        try:
            from ..api.bridge import register_bridge_routes
            from ..services.modules import ModuleCapability, is_module_enabled
        except (ImportError, ValueError):
            from api.bridge import register_bridge_routes
            from services.modules import ModuleCapability, is_module_enabled

        if hasattr(server, "app") and is_module_enabled(ModuleCapability.BRIDGE):
            register_bridge_routes(server.app)
            print("[OpenClaw] Bridge routes registered")
        elif not is_module_enabled(ModuleCapability.BRIDGE):
            print("[OpenClaw] Bridge module disabled; skipping route registration")
    except ImportError:
        pass

    _run_mae_startup_gate(server)

    # S8/S23/F11 Asset Packs
    # R84 Boot Boundary: REGISTRY_SYNC (Packs management)
    try:
        try:
            from ..api.packs import PacksHandlers
            from ..services.modules import ModuleCapability, is_module_enabled
        except (ImportError, ValueError):
            from api.packs import PacksHandlers
            from services.modules import ModuleCapability, is_module_enabled

        # Packs are currently treated as part of CORE or REGISTRY_SYNC depending on strictness.
        # For now, we bind them to REGISTRY_SYNC if we want to segment them,
        # but realistically they are often core local features.
        # Let's check REGISTRY_SYNC for import/export features specifically if we wanted to split,
        # but keeping them enabled by default for now unless R84 explicitly segments them.
        # DESIGN DECISION: Packs are local core features. Registry sync is remote.
        # We will keep basic pack routes, but R84 might control remote interactions later.

        try:
            from ..config import DATA_DIR
        except (ImportError, ValueError):
            from config import DATA_DIR

        packs = PacksHandlers(DATA_DIR)

        for prefix in prefixes:
            pack_routes = [
                ("GET", f"{prefix}/packs", packs.list_packs_handler),
                ("POST", f"{prefix}/packs/import", packs.import_pack_handler),
                (
                    "GET",
                    f"{prefix}/packs/export/{{name}}/{{version}}",
                    packs.export_pack_handler,
                ),
                (
                    "DELETE",
                    f"{prefix}/packs/{{name}}/{{version}}",
                    packs.delete_pack_handler,
                ),
            ]

            for method, path, handler in pack_routes:
                register_dual_route(server, method, path, handler)

    except ImportError:
        pass
