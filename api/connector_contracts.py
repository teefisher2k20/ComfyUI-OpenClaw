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
    from ..services.access_control import require_admin_token
    from ..services.connector_installation_registry import (
        get_connector_installation_registry,
    )
    from ..services.rate_limit import check_rate_limit
else:  # pragma: no cover
    from services.access_control import require_admin_token  # type: ignore
    from services.connector_installation_registry import (  # type: ignore
        get_connector_installation_registry,
    )
    from services.rate_limit import check_rate_limit  # type: ignore

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
        return web.json_response({"ok": False, "error": "Rate limit exceeded"}, status=429)
    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response({"ok": False, "error": err or "Unauthorized"}, status=403)
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
    installations = registry.list_installations(
        platform=platform, workspace_id=workspace_id, status=status
    )
    return web.json_response(
        {
            "ok": True,
            "installations": [inst.to_public_dict() for inst in installations],
            "diagnostics": registry.diagnostics(),
        }
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
    installation = registry.get_installation(installation_id)
    if installation is None:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    return web.json_response({"ok": True, "installation": installation.to_public_dict()})


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
    resolution = registry.resolve_installation(platform, workspace_id)
    status_code = 200 if resolution.ok else 409
    return web.json_response({"ok": resolution.ok, "resolution": resolution.to_public_dict()}, status=status_code)


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
    return web.json_response(
        {
            "ok": True,
            "events": registry.get_audit_trail(
                installation_id=installation_id, limit=limit
            ),
        }
    )
