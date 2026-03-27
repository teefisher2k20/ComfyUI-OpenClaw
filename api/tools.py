"""
S12: API Handlers for External Tools.
Protected by Admin Token and Feature Flag.
"""

from __future__ import annotations

import json
import logging

try:
    from ..services.access_control import require_admin_token, resolve_token_info
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.audit import emit_audit_event
    from ..services.tool_runner import get_tool_runner, is_tools_enabled
except ImportError:
    from services.access_control import require_admin_token  # type: ignore
    from services.access_control import resolve_token_info  # type: ignore
    from services.aiohttp_compat import import_aiohttp_web  # type: ignore
    from services.audit import emit_audit_event  # type: ignore
    from services.tool_runner import get_tool_runner, is_tools_enabled

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

logger = logging.getLogger("ComfyUI-OpenClaw.api.tools")
web = import_aiohttp_web()


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="List tools",
    description="List allowed external tools.",
    audit="tools.list",
    plane=RoutePlane.ADMIN,
)
async def tools_list_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/tools
    List allowed external tools.
    Requires: Admin Token.
    """
    if not is_tools_enabled():
        return web.json_response(
            {"ok": False, "error": "External tooling is disabled (feature flag off)."},
            status=404,  # Not Found or Forbidden? 404 implies feature doesn't exist.
        )

    # Admin check
    allowed, error = require_admin_token(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    runner = get_tool_runner()
    tools = runner.list_tools()

    return web.json_response({"ok": True, "tools": tools})


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Run tool",
    description="Execute an external tool.",
    audit="tools.run",
    plane=RoutePlane.ADMIN,
)
async def tools_run_handler(request: web.Request) -> web.Response:
    """
    POST /openclaw/tools/{name}/run
    Execute an external tool.
    Body: {"args": {"arg1": "val1", ...}}
    Requires: Admin Token.
    """
    # S62: Block tool execution in public+split mode
    try:
        # CRITICAL: package-relative import must stay first in ComfyUI runtime.
        from ..services.surface_guard import check_surface
    except ImportError:
        from services.surface_guard import check_surface  # type: ignore
    blocked = check_surface("tool_execution", request)
    if blocked:
        return blocked

    if not is_tools_enabled():
        emit_audit_event(
            action="tools.run",
            target="tools",
            outcome="deny",
            status_code=404,
            details={"reason": "tools_disabled"},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": "External tooling is disabled."}, status=404
        )

    token_info = resolve_token_info(request)

    # Admin check
    allowed, error = require_admin_token(request)
    if not allowed:
        emit_audit_event(
            action="tools.run",
            target="tools",
            outcome="deny",
            token_info=token_info,
            status_code=403,
            details={"reason": error or "unauthorized"},
            request=request,
        )
        return web.json_response({"ok": False, "error": error}, status=403)

    tool_name = request.match_info.get("name")
    if not tool_name:
        emit_audit_event(
            action="tools.run",
            target="unknown",
            outcome="deny",
            token_info=token_info,
            status_code=400,
            details={"reason": "missing_tool_name"},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": "Tool name required"}, status=400
        )

    try:
        body = await request.json()
    except json.JSONDecodeError:
        emit_audit_event(
            action="tools.run",
            target=tool_name,
            outcome="deny",
            token_info=token_info,
            status_code=400,
            details={"reason": "invalid_json"},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": "Invalid JSON body"}, status=400
        )

    args = body.get("args", {})
    if not isinstance(args, dict):
        emit_audit_event(
            action="tools.run",
            target=tool_name,
            outcome="deny",
            token_info=token_info,
            status_code=400,
            details={"reason": "invalid_args_shape"},
            request=request,
        )
        return web.json_response(
            {"ok": False, "error": "'args' must be a dictionary"}, status=400
        )

    runner = get_tool_runner()
    result = runner.execute_tool(tool_name, args)

    if not result.success:
        emit_audit_event(
            action="tools.run",
            target=tool_name,
            outcome="error",
            token_info=token_info,
            status_code=500 if result.error else 400,
            details={
                "exit_code": result.exit_code,
                "error": result.error,
                "duration_ms": result.duration_ms,
            },
            request=request,
        )
        return web.json_response(
            {
                "ok": False,
                "tool": tool_name,
                "error": result.error,
                "output": result.output,  # Redacted output might contain useful error info
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
            },
            status=500 if result.error else 400,
        )  # 500 for runtime error, 400 for validation?

    emit_audit_event(
        action="tools.run",
        target=tool_name,
        outcome="allow",
        token_info=token_info,
        status_code=200,
        details={"duration_ms": result.duration_ms},
        request=request,
    )
    return web.json_response(
        {
            "ok": True,
            "tool": tool_name,
            "output": result.output,
            "duration_ms": result.duration_ms,
        }
    )
