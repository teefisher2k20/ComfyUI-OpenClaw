"""
F61 Remote Admin Console static page handler.
Serves a standalone mobile-friendly admin UI for remote operators.
"""

from __future__ import annotations

from pathlib import Path

if __package__ and "." in __package__:
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
else:  # pragma: no cover (test-only import mode)
    from services.aiohttp_compat import import_aiohttp_web  # type: ignore
    from services.endpoint_manifest import (  # type: ignore
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )

web = import_aiohttp_web()


# CRITICAL: resolve HTML path relative to this module's package root.
# Do not switch to cwd-based resolution; ComfyUI may launch from arbitrary directories.
def _admin_console_html_path() -> Path:
    return Path(__file__).resolve().parents[1] / "web" / "admin_console.html"


@endpoint_metadata(
    auth=AuthTier.PUBLIC,
    risk=RiskTier.LOW,
    summary="Remote admin console page",
    description="Serves the standalone remote admin console HTML shell.",
    audit="admin.console.page",
    plane=RoutePlane.USER,
)
async def remote_admin_page_handler(request: web.Request) -> web.Response:
    path = _admin_console_html_path()
    if not path.exists():
        return web.json_response(
            {
                "ok": False,
                "error": "remote_admin_console_not_found",
                "path": str(path),
            },
            status=500,
        )

    html = path.read_text(encoding="utf-8")
    return web.Response(
        text=html,
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )
