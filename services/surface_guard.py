"""
S62 Runtime Surface Guard.

Provides a reusable guard function for blocking high-risk API surfaces
when the control-plane is in split mode. Returns a structured 403 response
with machine-readable error code and remediation guidance.

Security policy:
- profile=local: fail-open on errors (backward compat)
- profile=public/hardened: fail-CLOSED on errors (security-first)
"""

from __future__ import annotations

import logging
import os

try:
    from .aiohttp_compat import import_aiohttp_web
except ImportError:
    from aiohttp_compat import import_aiohttp_web  # type: ignore

web = import_aiohttp_web()

logger = logging.getLogger(__name__)


def _is_fail_closed_profile() -> bool:
    """Return True if errors should fail-closed (block) rather than fail-open."""
    profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local").lower()
    if profile == "public":
        return True
    try:
        from .runtime_profile import is_hardened_mode
    except ImportError:
        from runtime_profile import is_hardened_mode  # type: ignore
    return bool(is_hardened_mode())


def check_surface(surface_id: str, request: web.Request = None) -> web.Response | None:
    """
    Check if a surface is blocked in the current control-plane configuration.

    Args:
        surface_id: One of the HIGH_RISK_SURFACES identifiers
                    (e.g. "webhook_execute", "secrets_write").
        request: Optional aiohttp request for audit logging.

    Returns:
        None if the surface is allowed (caller proceeds normally).
        web.Response (403) if the surface is blocked.
    """
    try:
        from .control_plane import get_blocked_surfaces, resolve_control_plane_mode

        profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local")
        mode = resolve_control_plane_mode(profile)
        blocked = get_blocked_surfaces(profile, mode)
        blocked_ids = {sid: desc for sid, desc in blocked}

        if surface_id not in blocked_ids:
            return None

        reason = blocked_ids[surface_id]
        logger.warning(
            f"S62: Blocked surface '{surface_id}' ({reason}) in "
            f"profile={profile}, mode={mode.value}"
        )

        # Structured 403 response with remediation
        return web.json_response(
            {
                "ok": False,
                "error": f"S62: Surface '{surface_id}' is blocked in split mode.",
                "code": "SURFACE_BLOCKED",
                "surface_id": surface_id,
                "reason": reason,
                "deployment_profile": profile,
                "control_plane_mode": mode.value,
                "remediation": (
                    f"This operation ({reason}) is delegated to the external "
                    "control plane in public+split mode. Use the external "
                    "control plane API, or set "
                    "OPENCLAW_SPLIT_COMPAT_OVERRIDE=1 for dev-only bypass."
                ),
            },
            status=403,
        )

    except ImportError:
        # control_plane module not available
        if _is_fail_closed_profile():
            logger.error(
                f"S62: control_plane module unavailable in non-local profile; "
                f"blocking surface '{surface_id}' (fail-closed)"
            )
            return web.json_response(
                {
                    "ok": False,
                    "error": f"S62: Surface '{surface_id}' blocked (control_plane module unavailable).",
                    "code": "SURFACE_BLOCKED",
                    "surface_id": surface_id,
                    "reason": "control_plane module not loaded in non-local profile",
                    "remediation": "Ensure control_plane.py is present or switch to local profile.",
                },
                status=403,
            )
        return None

    except Exception as e:
        logger.error(f"S62: Surface guard error: {e}")
        if _is_fail_closed_profile():
            logger.error(
                f"S62: Failing closed for surface '{surface_id}' due to guard error"
            )
            return web.json_response(
                {
                    "ok": False,
                    "error": f"S62: Surface '{surface_id}' blocked (guard error in non-local profile).",
                    "code": "SURFACE_BLOCKED",
                    "surface_id": surface_id,
                    "reason": f"Guard error: {e}",
                    "remediation": "Check logs for control_plane errors or switch to local profile.",
                },
                status=403,
            )
        return None
