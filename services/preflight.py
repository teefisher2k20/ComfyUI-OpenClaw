"""
Preflight Diagnostics Service (R42).

Provides logic to validate a workflow against the local ComfyUI environment,
checking for missing node classes and models.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.preflight")

# IMPORTANT (ComfyUI runtime wiring):
# This module is imported both:
# - inside a real ComfyUI runtime (where `nodes` and `folder_paths` exist), and
# - in unit tests / tooling contexts (where they may not).
# Keep these imports optional and keep references guarded.
try:  # pragma: no cover (best-effort ComfyUI imports)
    import nodes  # type: ignore
except Exception:  # pragma: no cover
    nodes = None  # type: ignore

try:  # pragma: no cover (best-effort ComfyUI imports)
    import folder_paths  # type: ignore
except Exception:  # pragma: no cover
    folder_paths = None  # type: ignore

_CACHE = {}
_CACHE_TTL = 60  # seconds
_INVENTORY_SCAN_STATE_IDLE = "idle"
_INVENTORY_SCAN_STATE_REFRESHING = "refreshing"
_INVENTORY_SCAN_STATE_ERROR = "error"
_INVENTORY_SNAPSHOT_KEY = "inventory_snapshot"
_INVENTORY_SNAPSHOT_TS_KEY = "inventory_snapshot_ts"
_INVENTORY_LAST_ERROR_KEY = "inventory_last_error"
_INVENTORY_SCAN_STATE_KEY = "inventory_scan_state"
_INVENTORY_CHECKPOINT_KEY = "inventory_scan_checkpoint"
_INVENTORY_LAST_ATTEMPT_TS_KEY = "inventory_last_attempt_ts"
_LEGACY_INVENTORY_CACHE_KEY = "inventory"
_INVENTORY_LOCK = threading.RLock()
_INVENTORY_SCAN_THREAD: threading.Thread | None = None
_INVENTORY_ERROR_RETRY_SEC = 5

# Heuristic mapping: input_key -> folder_paths type
_INPUT_KEY_MAP = {
    "ckpt_name": "checkpoints",
    "checkpoint": "checkpoints",
    "lora_name": "loras",
    "vae_name": "vae",
    "control_net_name": "controlnet",
    "upscale_model_name": "upscale_models",
    "style_model_name": "style_models",
    "clip_name": "clip",
    "unet_name": "unet",
    # Add more as discovered
}


def _get_node_class_mappings() -> Dict[str, Any]:
    """Safely retrieve the global NODE_CLASS_MAPPINGS."""
    if nodes and hasattr(nodes, "NODE_CLASS_MAPPINGS"):
        return nodes.NODE_CLASS_MAPPINGS
    return {}


def _resolve_inventory_model_types() -> List[str]:
    model_types = [
        "checkpoints",
        "loras",
        "vae",
        "embeddings",
        "controlnet",
        "upscale_models",
        "clip",
        "unet",
        "clip_vision",
        "style_models",
        "diffusers",
        "vae_approx",
        "photomaker",
    ]
    if hasattr(folder_paths, "folder_names_and_paths"):
        for key in folder_paths.folder_names_and_paths.keys():
            if key not in model_types:
                model_types.append(key)
    return model_types


def _scan_model_inventory(checkpoint: List[str] | None = None) -> Dict[str, List[str]]:
    """
    Build a complete model inventory snapshot synchronously.

    The caller decides whether this runs on-request or in a background worker.
    """
    inventory: Dict[str, List[str]] = {}
    if not folder_paths:
        return inventory

    model_types = _resolve_inventory_model_types()
    for index, model_type in enumerate(model_types):
        if checkpoint is not None:
            checkpoint[:] = [str(index), model_type]
        try:
            files = folder_paths.get_filename_list(model_type)
            if files:
                inventory[model_type] = list(files)
        except Exception:
            # Some folders might not exist or raise error.
            continue
    if checkpoint is not None:
        checkpoint[:] = []
    return inventory


def _copy_inventory_snapshot(models: Dict[str, List[str]]) -> Dict[str, List[str]]:
    return {key: list(value) for key, value in (models or {}).items()}


def _inventory_snapshot_stale_locked(now: float | None = None) -> bool:
    snapshot_ts = _CACHE.get(_INVENTORY_SNAPSHOT_TS_KEY)
    if not snapshot_ts:
        return True
    current = time.time() if now is None else now
    return current - float(snapshot_ts) >= _CACHE_TTL


def _inventory_scan_running_locked() -> bool:
    global _INVENTORY_SCAN_THREAD
    if _INVENTORY_SCAN_THREAD is not None and not _INVENTORY_SCAN_THREAD.is_alive():
        _INVENTORY_SCAN_THREAD = None
    return _INVENTORY_SCAN_THREAD is not None


def _inventory_should_schedule_refresh_locked(now: float) -> bool:
    if not folder_paths or not _inventory_snapshot_stale_locked(now):
        return False
    if _inventory_scan_running_locked():
        return False
    if _CACHE.get(_INVENTORY_SCAN_STATE_KEY) != _INVENTORY_SCAN_STATE_ERROR:
        return True
    last_attempt = float(_CACHE.get(_INVENTORY_LAST_ATTEMPT_TS_KEY) or 0.0)
    return now - last_attempt >= _INVENTORY_ERROR_RETRY_SEC


def _inventory_refresh_worker() -> None:
    checkpoint: List[str] = []
    try:
        snapshot = _scan_model_inventory(checkpoint)
        with _INVENTORY_LOCK:
            _CACHE[_INVENTORY_SNAPSHOT_KEY] = snapshot
            _CACHE[_INVENTORY_SNAPSHOT_TS_KEY] = time.time()
            _CACHE[_INVENTORY_LAST_ERROR_KEY] = None
            _CACHE[_INVENTORY_SCAN_STATE_KEY] = _INVENTORY_SCAN_STATE_IDLE
            _CACHE[_INVENTORY_CHECKPOINT_KEY] = None
    except Exception as exc:  # pragma: no cover - defensive outer guard
        with _INVENTORY_LOCK:
            _CACHE[_INVENTORY_LAST_ERROR_KEY] = str(exc)
            _CACHE[_INVENTORY_SCAN_STATE_KEY] = _INVENTORY_SCAN_STATE_ERROR
            _CACHE[_INVENTORY_CHECKPOINT_KEY] = (
                checkpoint[1] if len(checkpoint) >= 2 else None
            )
            logger.exception("Inventory deep scan failed")
    finally:
        global _INVENTORY_SCAN_THREAD
        with _INVENTORY_LOCK:
            _INVENTORY_SCAN_THREAD = None


def _schedule_inventory_refresh_locked() -> None:
    global _INVENTORY_SCAN_THREAD
    if _inventory_scan_running_locked() or not folder_paths:
        return
    _CACHE[_INVENTORY_SCAN_STATE_KEY] = _INVENTORY_SCAN_STATE_REFRESHING
    _CACHE.setdefault(_INVENTORY_LAST_ERROR_KEY, None)
    _CACHE[_INVENTORY_LAST_ATTEMPT_TS_KEY] = time.time()
    worker = threading.Thread(
        target=_inventory_refresh_worker,
        name="openclaw-inventory-refresh",
        daemon=True,
    )
    _INVENTORY_SCAN_THREAD = worker
    worker.start()


def get_model_inventory_snapshot(*, trigger_refresh: bool = True) -> Dict[str, Any]:
    """
    Return the latest served inventory snapshot plus scan metadata.

    This powers `/openclaw/preflight/inventory` so requests can return quickly
    while a background deep scan refreshes stale or missing snapshots.
    """
    now = time.time()
    with _INVENTORY_LOCK:
        if trigger_refresh and _inventory_should_schedule_refresh_locked(now):
            _schedule_inventory_refresh_locked()

        models = _copy_inventory_snapshot(_CACHE.get(_INVENTORY_SNAPSHOT_KEY, {}))
        snapshot_ts = _CACHE.get(_INVENTORY_SNAPSHOT_TS_KEY)
        scan_state = _CACHE.get(_INVENTORY_SCAN_STATE_KEY, _INVENTORY_SCAN_STATE_IDLE)
        last_error = _CACHE.get(_INVENTORY_LAST_ERROR_KEY)
        if not folder_paths:
            scan_state = _INVENTORY_SCAN_STATE_IDLE
            last_error = None
        stale = bool(folder_paths) and _inventory_snapshot_stale_locked(now)
        return {
            "models": models,
            "snapshot_ts": snapshot_ts,
            "scan_state": scan_state,
            "stale": stale,
            "last_error": last_error,
        }


def _reset_inventory_state_for_tests() -> None:
    global _INVENTORY_SCAN_THREAD
    thread = _INVENTORY_SCAN_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    with _INVENTORY_LOCK:
        _INVENTORY_SCAN_THREAD = None
        for key in (
            _INVENTORY_SNAPSHOT_KEY,
            _INVENTORY_SNAPSHOT_TS_KEY,
            _INVENTORY_LAST_ERROR_KEY,
            _INVENTORY_SCAN_STATE_KEY,
            _INVENTORY_CHECKPOINT_KEY,
            _INVENTORY_LAST_ATTEMPT_TS_KEY,
            _LEGACY_INVENTORY_CACHE_KEY,
        ):
            _CACHE.pop(key, None)


def _get_model_inventory() -> Dict[str, List[str]]:
    """
    Retrieve snapshot of available models using folder_paths.
    Returns a dict mapping folder name (e.g., 'checkpoints') to list of filenames.
    Cached for 60s to prevent IO spam.
    """
    global _CACHE
    now = time.time()

    cached = _CACHE.get(_LEGACY_INVENTORY_CACHE_KEY)
    if cached:
        timestamp, data = cached
        if now - timestamp < _CACHE_TTL:
            return data
    inventory = _scan_model_inventory()
    _CACHE[_LEGACY_INVENTORY_CACHE_KEY] = (now, inventory)
    return inventory


def run_preflight_check(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze a workflow (API format) and return a diagnostic report.

    Args:
        workflow: The ComfyUI workflow JSON (node ID -> node data).

    Returns:
        Dict containing validation results (missing_nodes, missing_models, etc.)
    """
    report = {
        "ok": True,
        "summary": {"missing_nodes": 0, "missing_models": 0, "invalid_inputs": 0},
        "missing_nodes": [],
        "missing_models": [],
        "invalid_inputs": [],
        "notes": [],
    }

    if not isinstance(workflow, dict):
        report["ok"] = False
        report["notes"].append("Workflow must be a JSON object (API format).")
        return report

    # 1. Check Nodes
    available_nodes = _get_node_class_mappings()
    missing_node_counts: Dict[str, int] = {}

    # 2. Check Models (Heuristic)
    inventory = _get_model_inventory()
    missing_models_counts: Dict[str, Dict[str, Any]] = {}

    for node_id, node_data in workflow.items():
        if not isinstance(node_data, dict):
            continue

        # Check Node Class
        class_type = node_data.get("class_type")
        if not class_type:
            continue

        if available_nodes and class_type not in available_nodes:
            missing_node_counts[class_type] = missing_node_counts.get(class_type, 0) + 1

        # Check Inputs for Models
        inputs = node_data.get("inputs")
        if isinstance(inputs, dict):
            _check_inputs_for_models(inputs, inventory, missing_models_counts)

    # Format Results
    for cls, count in missing_node_counts.items():
        report["missing_nodes"].append({"class_type": cls, "count": count})

    for key, info in missing_models_counts.items():
        report["missing_models"].append(
            {"type": info["type"], "name": info["name"], "count": info["count"]}
        )

    # Summarize
    report["summary"]["missing_nodes"] = len(report["missing_nodes"])
    report["summary"]["missing_models"] = len(report["missing_models"])

    if (
        report["summary"]["missing_nodes"] > 0
        or report["summary"]["missing_models"] > 0
    ):
        report["ok"] = False

    if not nodes:
        report["notes"].append("Node inventory unavailable (backend import failed).")
    if not folder_paths:
        report["notes"].append("Model inventory unavailable (backend import failed).")

    # F49: Inject Guidance Banners
    # We serialize them so they are ready for JSON response
    banners = generate_preflight_banners(report)
    report["banners"] = [b.to_dict() for b in banners]

    return report


def _check_inputs_for_models(
    inputs: Dict[str, Any],
    inventory: Dict[str, List[str]],
    missing_counts: Dict[str, Dict[str, Any]],
):
    """
    Heuristic to detect missing models in node inputs.
    We look for keys that hint at model types (e.g. 'ckpt_name', 'lora_name').
    """
    # Mapping heuristic: input_key -> folder_paths type
    key_map = _INPUT_KEY_MAP

    for key, value in inputs.items():
        if not isinstance(value, str):
            continue

        target_type = key_map.get(key)
        if target_type:
            # Check if exists
            available = inventory.get(target_type, [])
            if value not in available:
                # Also try normalizing separators just in case (e.g. windows vs linux paths)
                # But typically ComfyUI expects exact match or relative match.
                # Use simple exact match for now.

                unique_key = f"{target_type}:{value}"

                if unique_key not in missing_counts:
                    missing_counts[unique_key] = {
                        "type": target_type,
                        "name": value,
                        "count": 0,
                    }
                missing_counts[unique_key]["count"] += 1


# F49: Banner Generation Support
def generate_preflight_banners(report: Dict[str, Any]) -> List["OperatorBanner"]:
    """
    Generate actionable guidance banners from a preflight report.
    Returns list of OperatorBanner objects.
    """
    # CRITICAL: keep package-relative import first.
    # Direct `services.*` imports can fail when loaded as a ComfyUI package module.
    if __package__ and "." in __package__:
        from .operator_guidance import BannerSeverity, OperatorAction, OperatorBanner
    else:  # pragma: no cover (standalone/test import mode)
        from services.operator_guidance import (  # type: ignore
            BannerSeverity,
            OperatorAction,
            OperatorBanner,
        )

    banners = []

    if report.get("ok"):
        return banners

    # 1. Missing Nodes
    missing_nodes = report.get("missing_nodes", [])
    # Sort for determinism
    missing_nodes.sort(key=lambda x: x["class_type"])

    if missing_nodes:
        node_names = [n["class_type"] for n in missing_nodes]
        count = len(node_names)
        preview = ", ".join(node_names[:3])
        if count > 3:
            preview += f" and {count - 3} more"

        banners.append(
            OperatorBanner(
                id="missing_nodes",
                severity=BannerSeverity.ERROR,
                message=f"Workflow requires missing custom nodes: {preview}",
                source="Preflight",
                action=OperatorAction(
                    label="Manager",
                    type="tab",
                    payload="manager",  # Future: deep link to manager
                ).to_dict(),
            )
        )

    # 2. Missing Models
    missing_models = report.get("missing_models", [])
    # Sort for determinism
    missing_models.sort(key=lambda x: (x["type"], x["name"]))

    if missing_models:
        model_names = [f"{m['name']} ({m['type']})" for m in missing_models]
        count = len(model_names)
        preview = ", ".join(model_names[:3])
        if count > 3:
            preview += f" and {count - 3} more"

        banners.append(
            OperatorBanner(
                id="missing_models",
                severity=BannerSeverity.WARNING,
                message=f"Workflow refers to missing models: {preview}",
                source="Preflight",
                # No specific action for models yet, maybe just docs or upload.
            )
        )

    # 3. Notes/Errors
    notes = report.get("notes", [])
    for i, note in enumerate(notes):
        banners.append(
            OperatorBanner(
                id=f"preflight_note_{i}",
                severity=BannerSeverity.WARNING,
                message=note,
                source="Preflight",
            )
        )

    return banners
