"""
ComfyUI History Service (F17).
Parses ComfyUI /history/{prompt_id} responses and extracts image output metadata.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:
    urlopen = None  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.services.comfyui_history")

COMFYUI_URL = (
    os.environ.get("OPENCLAW_COMFYUI_URL")
    or os.environ.get("MOLTBOT_COMFYUI_URL")
    or "http://127.0.0.1:8188"
)
HISTORY_TIMEOUT = 5


def fetch_history(prompt_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch history for a given prompt_id from ComfyUI.
    Returns the history item dict if found, else None.
    """
    url = f"{COMFYUI_URL}/history/{prompt_id}"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=HISTORY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get(prompt_id)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as e:
        logger.warning(f"Failed to fetch history for {prompt_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching history: {e}")
        return None


def extract_images(history_item: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Extract image outputs from a history item.
    Returns list of { filename, subfolder, type, view_url }.
    """
    results = []
    outputs = history_item.get("outputs", {})

    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        for img in images:
            asset_hash = ""
            if isinstance(img.get("asset_hash"), str):
                asset_hash = img.get("asset_hash", "").strip()
            elif isinstance(img.get("asset"), dict):
                asset_hash = str(img.get("asset", {}).get("asset_hash", "")).strip()

            filename = img.get("filename") or img.get("name") or asset_hash
            subfolder = img.get("subfolder", "")
            img_type = img.get("type", "output")

            if not filename:
                continue

            # IMPORTANT: asset-backed refs must still resolve through /view so
            # callback consumers stay compatible with classic history behavior.
            if asset_hash:
                params = {"filename": asset_hash}
            else:
                params = {"filename": filename, "type": img_type}
                if subfolder:
                    params["subfolder"] = subfolder

            view_url = f"{COMFYUI_URL}/view?{urlencode(params)}"

            results.append(
                {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": img_type,
                    "asset_hash": asset_hash,
                    "view_url": view_url,
                }
            )

    return results


def get_job_status(history_item: Optional[Dict[str, Any]]) -> str:
    """
    Determine job status from history item.
    Returns: 'pending', 'running', 'completed', 'error', 'unknown'.
    """
    if history_item is None:
        return "pending"  # Not yet in history

    status = history_item.get("status", {})
    status_str = status.get("status_str", "")

    if status_str == "success":
        return "completed"
    elif status_str == "error":
        return "error"
    elif history_item.get("outputs"):
        return "completed"

    return "unknown"
