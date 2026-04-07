"""
Checkpoints Service (R47).
Manages local workflow snapshots for safe iteration.
Implements limits (count/size) and oldest-eviction policy.
"""

import json
import logging
import os
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    from config import DATA_DIR
except ImportError:
    # Fallback for tests or decoupled run
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

from .integrity import IntegrityError, load_verified, save_verified
from .safe_io import PathTraversalError, resolve_under_root

logger = logging.getLogger("ComfyUI-OpenClaw.services.checkpoints")

CHECKPOINTS_DIR = os.path.join(DATA_DIR, "checkpoints")
MAX_CHECKPOINTS = 50
MAX_PAYLOAD_SIZE = 1 * 1024 * 1024  # 1MB


def _ensure_dir():
    os.makedirs(CHECKPOINTS_DIR, exist_ok=True)


def _normalize_checkpoint_id(checkpoint_id: str) -> str:
    text = str(checkpoint_id or "").strip()
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("invalid checkpoint id") from exc


def _get_paths(checkpoint_id: str) -> Tuple[str, str]:
    """Return (meta_path, payload_path) for a given ID."""
    cid = _normalize_checkpoint_id(checkpoint_id)
    return (
        resolve_under_root(CHECKPOINTS_DIR, f"{cid}.meta.json"),
        resolve_under_root(CHECKPOINTS_DIR, f"{cid}.workflow.json"),
    )


def list_checkpoints() -> List[Dict[str, Any]]:
    """List all checkpoints (metadata only), sorted by updated_at desc."""
    _ensure_dir()
    checkpoints = []

    try:
        with os.scandir(CHECKPOINTS_DIR) as it:
            for entry in it:
                if entry.name.endswith(".meta.json") and entry.is_file():
                    try:

                        meta = load_verified(entry.path, migrate=True)
                        checkpoints.append(meta)
                    except IntegrityError as e:
                        logger.critical(
                            f"R77: Integrity violation in checkpoint {entry.name}: {e}"
                        )
                        # Skip this file (fail-closed for this item)
                    except Exception:
                        logger.warning(f"Failed to read checkpoint meta: {entry.name}")
    except OSError:
        return []

    # Sort by timestamp desc (newest first)
    checkpoints.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return checkpoints


def get_checkpoint(checkpoint_id: str) -> Optional[Dict[str, Any]]:
    """Get full checkpoint data (meta + workflow)."""
    try:
        meta_path, payload_path = _get_paths(checkpoint_id)
    except (ValueError, PathTraversalError):
        return None

    if not os.path.exists(meta_path) or not os.path.exists(payload_path):
        return None

    try:

        meta = load_verified(meta_path, migrate=True)
        workflow = load_verified(payload_path, migrate=True)

        return {"id": checkpoint_id, "meta": meta, "workflow": workflow}
    except IntegrityError as e:
        logger.critical(f"R77: Integrity violation in checkpoint {checkpoint_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading checkpoint {checkpoint_id}: {e}")
        return None


import tempfile


def _atomic_write(filepath: str, content: str | bytes):
    """
    Write to a temp file then rename to ensure atomicity.
    Handles string (utf-8) or bytes.
    """
    mode = "wb" if isinstance(content, bytes) else "w"
    folder = os.path.dirname(filepath)
    prefix = os.path.basename(filepath) + ".tmp"

    # Create temp file in the same directory to ensure atomic rename works (same filesystem)
    fd, tmp_path = tempfile.mkstemp(
        prefix=prefix, dir=folder, text=not isinstance(content, bytes)
    )
    try:
        with os.fdopen(fd, mode, encoding="utf-8" if mode == "w" else None) as f:
            f.write(content)
        # Atomic rename
        os.replace(tmp_path, filepath)
    except Exception:
        # Cleanup if something failed/crashed before replace
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def create_checkpoint(
    name: str, workflow: Dict[str, Any], description: str = ""
) -> Dict[str, Any]:
    """
    Create a new checkpoint.
    Enforces size limits and eviction policy.
    """
    _ensure_dir()

    # 0. Validate Inputs
    if len(name) > 100:
        raise ValueError("Name exceeds 100 characters")
    if len(description) > 500:
        raise ValueError("Description exceeds 500 characters")

    # 1. Validate Size
    workflow_json = json.dumps(workflow)
    if len(workflow_json.encode("utf-8")) > MAX_PAYLOAD_SIZE:
        raise ValueError(f"Workflow exceeds max size of {MAX_PAYLOAD_SIZE} bytes")

    # 2. Eviction (if needed)
    current_list = list_checkpoints()
    if len(current_list) >= MAX_CHECKPOINTS:
        # Evict oldest (last in list)
        to_remove = current_list[-1]
        delete_checkpoint(to_remove["id"])

    # 3. Save
    cid = str(uuid.uuid4())
    timestamp = time.time()

    meta = {
        "id": cid,
        "name": name,
        "description": description,
        "timestamp": timestamp,
        "size_bytes": len(workflow_json.encode("utf-8")),
        "node_count": len(workflow) if isinstance(workflow, dict) else 0,
    }

    meta_path, payload_path = _get_paths(cid)

    try:
        save_verified(meta_path, meta)
        save_verified(payload_path, workflow)
    except Exception as e:
        # Cleanup on fail (although atomic write minimizes this risk for individual files)
        if os.path.exists(meta_path):
            os.remove(meta_path)
        if os.path.exists(payload_path):
            os.remove(payload_path)
        raise IOError(f"Failed to save checkpoint: {e}")

    return meta


def delete_checkpoint(checkpoint_id: str) -> bool:
    """Delete a checkpoint."""
    try:
        meta_path, payload_path = _get_paths(checkpoint_id)
    except (ValueError, PathTraversalError):
        return False

    deleted = False
    if os.path.exists(meta_path):
        os.remove(meta_path)
        deleted = True
    if os.path.exists(payload_path):
        os.remove(payload_path)
        deleted = True

    return deleted
