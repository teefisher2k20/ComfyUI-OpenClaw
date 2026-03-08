"""
Capabilities Service (R19).
Provides capability probing for frontend version compatibility.
"""

import os

if __package__ and "." in __package__:
    from ..config import PACK_NAME, PACK_VERSION
else:  # pragma: no cover (test-only import mode)
    from config import PACK_NAME, PACK_VERSION

from .runtime_profile import get_runtime_profile

API_VERSION = 1


def _get_control_plane_info() -> dict:
    """Build control-plane status for capabilities response."""
    try:
        import os

        from .control_plane import get_blocked_surfaces, resolve_control_plane_mode

        profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local")
        mode = resolve_control_plane_mode(profile)
        blocked = get_blocked_surfaces(profile, mode)
        info = {
            "mode": mode.value,
            "blocked_surfaces": [sid for sid, _ in blocked],
        }
        # Include adapter health in split mode
        if mode.value == "split":
            try:
                from .control_plane_adapter import ControlPlaneAdapter

                adapter = ControlPlaneAdapter.from_env()
                info["adapter_health"] = adapter.get_health()
            except Exception:
                info["adapter_health"] = {"configured": False}
        return info
    except Exception:
        return {"mode": "embedded", "blocked_surfaces": []}


def get_capabilities() -> dict:
    """
    Return capability surface for frontend probing.
    """
    cp_info = _get_control_plane_info()
    result = {
        "api_version": API_VERSION,
        "runtime_profile": get_runtime_profile().value,
        "control_plane": cp_info,
        "pack": {
            "name": PACK_NAME,
            "version": PACK_VERSION,
        },
        "features": {
            "webhook_submit": True,
            "logs_tail": True,
            # Legacy flag (kept for older frontends/tests).
            # Do not remove without a migration window + frontend update.
            "doctor": True,
            "job_monitor": True,
            "callback_delivery": True,
            "presets": True,
            "approvals": True,
            "assist_planner": True,
            "assist_refiner": True,
            "assist_streaming": True,  # R38 optional SSE-style assist streaming path
            "assist_automation_compose": True,
            "scheduler": True,
            "triggers": True,
            "packs": True,
            # R42/F28/R47: Explorer + Preflight + Checkpoints
            "explorer": True,
            "preflight": True,
            "checkpoints": True,
            "rewrite_recipes": True,  # F53
            "model_manager": True,  # F54
            # R70/F39/R73: Settings contract + UX degradation + Provider governance
            "settings_contract": True,
            "provider_governance": True,
            # F40/R71/R72: Webhook mapping + Job events + Operator doctor
            "webhook_mapping": True,
            "job_events": True,
            "operator_doctor": True,
        },
    }

    # F51: Action Capability Matrix
    # H2 (F55): Cross-match blocked surfaces with action names for frontend UX
    _surface_action_map = {
        "webhook_execute": "queue",
        "callback_egress": "queue",
        "secrets_write": "settings",
        "tool_execution": "doctor_fix",
    }
    blocked_surfaces = set(cp_info.get("blocked_surfaces", []))

    base_actions = {
        "doctor": {"enabled": True, "mutating": False},
        "doctor_fix": {"enabled": True, "mutating": True},
        "inspect": {"enabled": True, "mutating": False},
        "queue": {"enabled": True, "mutating": False},
        "settings": {"enabled": True, "mutating": False},
        "install_node": {"enabled": False, "mutating": True},
        "update_pack": {"enabled": False, "mutating": True},
    }

    for surface_id, action_name in _surface_action_map.items():
        if surface_id in blocked_surfaces and action_name in base_actions:
            base_actions[action_name] = {
                **base_actions[action_name],
                "enabled": False,
                "blocked_reason": (
                    f"Surface '{surface_id}' is blocked in split mode. "
                    "Use the external control plane or switch to local profile."
                ),
            }

    result["actions"] = base_actions
    return result
