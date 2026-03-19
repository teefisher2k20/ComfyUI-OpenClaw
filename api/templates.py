"""
Templates API (F29 support).

Provides a lightweight endpoint to list template IDs that can be used
with `/openclaw/triggers/fire` (and by chat connectors via `/run <template_id> ...`).
"""

from __future__ import annotations

import logging

try:
    from aiohttp import web  # type: ignore
except ImportError:  # pragma: no cover (optional for unit tests)
    web = None  # type: ignore

# Import discipline:
# - ComfyUI runtime: package-relative imports only (prevents collisions with other custom nodes).
# - Unit tests: allow top-level fallbacks.
if __package__ and "." in __package__:
    from ..services.access_control import (
        require_observability_access,
        resolve_token_info,
    )
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.templates import get_template_service
    from ..services.tenant_context import TenantBoundaryError, request_tenant_scope
else:  # pragma: no cover (test-only import mode)
    from services.access_control import (  # type: ignore
        require_observability_access,
        resolve_token_info,
    )
    from services.rate_limit import (  # type: ignore
        build_rate_limit_response,
        check_rate_limit,
    )
    from services.templates import get_template_service  # type: ignore
    from services.tenant_context import (  # type: ignore
        TenantBoundaryError,
        request_tenant_scope,
    )

# R98: Endpoint Metadata
if __package__ and "." in __package__:
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
else:
    from services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.templates")


def _ensure_templates_api_deps_ready() -> tuple[bool, str | None]:
    """
    Defensive guard against a recurring regression class:
    if import discipline is broken, these symbols can become `None`,
    causing aiohttp to emit noisy tracebacks and clients to see ERR_INVALID_RESPONSE.
    """
    missing = []
    if not callable(require_observability_access):
        missing.append("require_observability_access")
    if not callable(check_rate_limit):
        missing.append("check_rate_limit")
    if not callable(get_template_service):
        missing.append("get_template_service")
    if missing:
        return (
            False,
            "Backend not fully initialized (missing route dependencies: "
            + ", ".join(missing)
            + ").",
        )
    return True, None


@endpoint_metadata(
    auth=AuthTier.OBSERVABILITY,  # Actually guarded by require_observability_access (token/loopback)
    risk=RiskTier.LOW,
    summary="List templates",
    description="Returns templates visible to the backend.",
    audit="templates.list",
    plane=RoutePlane.ADMIN,
)
async def templates_list_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/templates (legacy: /moltbot/templates)

    Returns the templates visible to the backend (file-based discovery + optional manifest metadata).
    This is safe to expose under the observability boundary because it contains
    no secrets and does not return workflow bodies.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    ok, init_error = _ensure_templates_api_deps_ready()
    if not ok:
        return web.json_response({"ok": False, "error": init_error}, status=500)

    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    # Reuse the admin bucket to avoid unbounded enumeration from remote callers.
    if not check_rate_limit(request, "admin"):
        return build_rate_limit_response(
            request,
            "admin",
            web_module=web,
            error="Rate limit exceeded",
            include_ok=True,
        )

    try:
        svc = get_template_service()
        token_info = resolve_token_info(request)
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ):
            items = []
            # Prefer runtime discovery (file-based templates) so operators don't need
            # to maintain a separate allowlist file.
            for template_id in svc.get_debug_info().get("discovered_template_ids", []):  # type: ignore[call-arg]
                cfg = svc.get_template_config(template_id)  # type: ignore[arg-type]
                if cfg is None:
                    continue
                items.append(
                    {
                        "id": template_id,
                        "allowed_inputs": list(cfg.allowed_inputs or []),
                        "defaults": dict(cfg.defaults or {}),
                    }
                )
            items.sort(key=lambda x: x["id"])
            resp: dict = {"ok": True, "templates": items, "count": len(items)}

            # Optional diagnostics. This reveals absolute paths, so keep it opt-in.
            debug = request.query.get("debug", "").strip() in ("1", "true", "yes")
            if debug:
                try:
                    resp["debug"] = svc.get_debug_info()  # type: ignore[attr-defined]
                except Exception:
                    # If TemplateService interface changes, don't break the endpoint.
                    resp["debug"] = {"error": "debug_info_unavailable"}

            return web.json_response(resp)
    except TenantBoundaryError as e:
        return web.json_response(
            {"ok": False, "error": e.code, "message": str(e)},
            status=403,
        )
    except Exception as e:
        logger.exception("Failed to list templates")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
