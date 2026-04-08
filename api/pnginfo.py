"""
PNG Info API handler (R168).
POST /openclaw/pnginfo (legacy: /moltbot/pnginfo)
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from ..services.access_control import require_admin_token
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.async_utils import run_in_thread
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
    from ..services.pnginfo import PngInfoError, parse_image_metadata
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
except ImportError:  # pragma: no cover
    from services.access_control import require_admin_token  # type: ignore
    from services.aiohttp_compat import import_aiohttp_web  # type: ignore
    from services.async_utils import run_in_thread  # type: ignore
    from services.endpoint_manifest import (  # type: ignore
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
    from services.pnginfo import PngInfoError, parse_image_metadata  # type: ignore
    from services.rate_limit import (  # type: ignore
        build_rate_limit_response,
        check_rate_limit,
    )

web = import_aiohttp_web()


def _json(payload: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


def _require_admin(request: web.Request) -> Optional[web.Response]:
    ok, error = require_admin_token(request)
    if ok:
        return None
    return _json({"ok": False, "error": error or "unauthorized"}, 403)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="Parse image metadata",
    description="Extract A1111 or ComfyUI metadata from an uploaded image payload.",
    audit="pnginfo.parse",
    plane=RoutePlane.ADMIN,
)
async def pnginfo_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    if not check_rate_limit(request, "admin"):
        return build_rate_limit_response(
            request,
            "admin",
            web_module=web,
            error="Rate limit exceeded",
            include_ok=False,
        )
    try:
        payload = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    if not isinstance(payload, dict):
        return _json({"ok": False, "error": "invalid_payload"}, 400)
    try:
        result = await run_in_thread(parse_image_metadata, payload.get("image_b64", ""))
    except PngInfoError as exc:
        return _json({"ok": False, "error": exc.code, "detail": exc.detail}, exc.status)
    except Exception:
        return _json({"ok": False, "error": "internal_error"}, 500)
    return _json(result)
