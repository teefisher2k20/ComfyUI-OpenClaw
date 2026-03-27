"""
Capabilities API Handler (R19).
GET /openclaw/capabilities (legacy: /moltbot/capabilities)
"""

from __future__ import annotations

# Import discipline
if __package__ and "." in __package__:
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.capabilities import get_capabilities
else:
    from services.aiohttp_compat import import_aiohttp_web
    from services.capabilities import get_capabilities


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

web = import_aiohttp_web()


@endpoint_metadata(
    auth=AuthTier.PUBLIC,
    risk=RiskTier.LOW,
    summary="Get capabilities",
    description="Returns API version and feature flags.",
    audit="capabilities.list",
    plane=RoutePlane.USER,
)
async def capabilities_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/capabilities (legacy: /moltbot/capabilities)
    Returns API version and feature flags for frontend compatibility.
    """
    return web.json_response(get_capabilities())
