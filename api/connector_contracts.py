from __future__ import annotations

import json
import logging
from typing import Optional

try:
    from aiohttp import web
except ImportError:  # pragma: no cover

    class _MockResponse:
        def __init__(self, payload: dict, status: int = 200):
            self.status = status
            self.body = json.dumps(payload).encode("utf-8")

    class _MockWeb:
        class Request:
            pass

        @staticmethod
        def json_response(payload: dict, status: int = 200):
            return _MockResponse(payload, status=status)

    web = _MockWeb()  # type: ignore

if __package__ and "." in __package__:
    from ..services.access_control import require_admin_token, resolve_token_info
    from ..services.connector_installation_registry import (
        get_connector_installation_registry,
    )
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.tenant_context import TenantBoundaryError, request_tenant_scope
else:  # pragma: no cover
    from services.access_control import require_admin_token  # type: ignore
    from services.access_control import resolve_token_info  # type: ignore
    from services.connector_installation_registry import (  # type: ignore
        get_connector_installation_registry,
    )
    from services.rate_limit import (  # type: ignore
        build_rate_limit_response,
        check_rate_limit,
    )
    from services.tenant_context import (  # type: ignore
        TenantBoundaryError,
        request_tenant_scope,
    )

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

logger = logging.getLogger("ComfyUI-OpenClaw.api.connector_contracts")


def _require_admin(request) -> Optional[web.Response]:
    if not check_rate_limit(request, "admin"):
        return build_rate_limit_response(
            request,
            "admin",
            web_module=web,
            error="Rate limit exceeded",
            include_ok=True,
        )
    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {"ok": False, "error": err or "Unauthorized"}, status=403
        )
    return None


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="List connector installations",
    description="Returns redacted multi-workspace connector installation diagnostics.",
    audit="connector.installations.list",
    plane=RoutePlane.ADMIN,
)
async def connector_installations_list_handler(request):
    if (guard := _require_admin(request)) is not None:
        return guard
    registry = get_connector_installation_registry()
    platform = request.query.get("platform")
    workspace_id = request.query.get("workspace_id")
    status = request.query.get("status")
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            installations = registry.list_installations(
                platform=platform,
                tenant_id=tenant.tenant_id,
                workspace_id=workspace_id,
                status=status,
            )
            return web.json_response(
                {
                    "ok": True,
                    "tenant_id": tenant.tenant_id,
                    "installations": [inst.to_public_dict() for inst in installations],
                    "diagnostics": registry.diagnostics(tenant_id=tenant.tenant_id),
                }
            )
    except TenantBoundaryError as exc:
        return web.json_response(
            {"ok": False, "error": exc.code, "message": str(exc)},
            status=403,
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Get connector installation",
    description="Returns a single redacted connector installation record.",
    audit="connector.installations.get",
    plane=RoutePlane.ADMIN,
)
async def connector_installation_get_handler(request):
    if (guard := _require_admin(request)) is not None:
        return guard
    installation_id = request.match_info.get("installation_id", "")
    registry = get_connector_installation_registry()
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            installation = registry.get_installation(
                installation_id, tenant_id=tenant.tenant_id
            )
            if installation is None:
                return web.json_response(
                    {"ok": False, "error": "not_found"}, status=404
                )
            return web.json_response(
                {
                    "ok": True,
                    "tenant_id": tenant.tenant_id,
                    "installation": installation.to_public_dict(),
                }
            )
    except TenantBoundaryError as exc:
        return web.json_response(
            {"ok": False, "error": exc.code, "message": str(exc)},
            status=403,
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Resolve connector installation",
    description="Runs fail-closed workspace resolution for diagnostics without exposing token material.",
    audit="connector.installations.resolve",
    plane=RoutePlane.ADMIN,
)
async def connector_installation_resolve_handler(request):
    if (guard := _require_admin(request)) is not None:
        return guard
    platform = (request.query.get("platform") or "").strip()
    workspace_id = (request.query.get("workspace_id") or "").strip()
    if not platform or not workspace_id:
        return web.json_response(
            {"ok": False, "error": "platform and workspace_id are required"},
            status=400,
        )
    registry = get_connector_installation_registry()
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            resolution = registry.resolve_installation(
                platform, workspace_id, tenant_id=tenant.tenant_id
            )
            status_code = 200 if resolution.ok else 409
            return web.json_response(
                {
                    "ok": resolution.ok,
                    "tenant_id": tenant.tenant_id,
                    "resolution": resolution.to_public_dict(),
                },
                status=status_code,
            )
    except TenantBoundaryError as exc:
        return web.json_response(
            {"ok": False, "error": exc.code, "message": str(exc)},
            status=403,
        )


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Connector installation audit",
    description="Returns installation lifecycle audit evidence.",
    audit="connector.installations.audit",
    plane=RoutePlane.ADMIN,
)
async def connector_installation_audit_handler(request):
    if (guard := _require_admin(request)) is not None:
        return guard
    registry = get_connector_installation_registry()
    installation_id = request.query.get("installation_id")
    try:
        limit = int(request.query.get("limit") or 100)
    except Exception:
        limit = 100
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            return web.json_response(
                {
                    "ok": True,
                    "tenant_id": tenant.tenant_id,
                    "events": registry.get_audit_trail(
                        installation_id=installation_id,
                        tenant_id=tenant.tenant_id,
                        limit=limit,
                    ),
                }
            )
    except TenantBoundaryError as exc:
        return web.json_response(
            {"ok": False, "error": exc.code, "message": str(exc)},
            status=403,
        )
