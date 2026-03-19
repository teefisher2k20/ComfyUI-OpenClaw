"""
Internal catalog/installations helpers for the model manager facade.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def load_installations(*, installations_path: Path) -> List[Dict[str, Any]]:
    if not installations_path.exists():
        return []
    try:
        data = json.loads(installations_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_installations(
    *,
    installations_path: Path,
    atomic_json_write: Callable[[Path, Any], None],
    rows: List[Dict[str, Any]],
) -> None:
    atomic_json_write(installations_path, rows)


def collect_install_entries(
    *,
    manager: Any,
    tenant_id: Optional[str],
    default_tenant_id: str,
    norm_model_type: Callable[[str], str],
    norm_source: Callable[[str], str],
) -> List[Dict[str, Any]]:
    rows = []
    for rec in load_installations(installations_path=manager.installations_path):
        if not manager._tenant_ok(
            str(rec.get("tenant_id") or default_tenant_id), tenant_id
        ):
            continue
        rows.append(
            {
                "id": str(rec.get("model_id") or rec.get("id") or ""),
                "name": str(rec.get("name") or ""),
                "model_type": norm_model_type(str(rec.get("model_type") or "")),
                "source": norm_source(str(rec.get("source") or "managed_install")),
                "source_label": str(rec.get("source_label") or "Managed Install"),
                "installed": True,
                "download_url": str(rec.get("download_url") or ""),
                "sha256": str(rec.get("sha256") or "").lower(),
                "size_bytes": rec.get("size_bytes"),
                "tags": list(rec.get("tags") or []),
                "provenance": dict(rec.get("provenance") or {}),
                "installation_path": str(rec.get("installation_path") or ""),
                "tenant_id": str(rec.get("tenant_id") or default_tenant_id),
                "updated_at": float(
                    rec.get("installed_at") or rec.get("updated_at") or 0.0
                ),
            }
        )
    return [row for row in rows if row["id"] and row["name"]]


def collect_catalog_entries(
    *,
    manager: Any,
    tenant_id: Optional[str],
    default_tenant_id: str,
    norm_model_type: Callable[[str], str],
    norm_source: Callable[[str], str],
) -> List[Dict[str, Any]]:
    rows = []
    for path in sorted(manager.catalog_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        src = norm_source(str(payload.get("source") or path.stem))
        src_label = str(payload.get("source_label") or src)
        items = payload.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("tenant_id") or default_tenant_id)
            if not manager._tenant_ok(tid, tenant_id):
                continue
            model_id = str(item.get("id") or item.get("model_id") or "").strip()
            name = str(item.get("name") or model_id).strip()
            if not model_id or not name:
                continue
            rows.append(
                {
                    "id": model_id,
                    "name": name,
                    "model_type": norm_model_type(str(item.get("model_type") or "")),
                    "source": src,
                    "source_label": src_label,
                    "installed": False,
                    "download_url": str(item.get("download_url") or ""),
                    "sha256": str(item.get("sha256") or "").lower(),
                    "size_bytes": item.get("size_bytes"),
                    "tags": list(item.get("tags") or []),
                    "provenance": dict(item.get("provenance") or {}),
                    "installation_path": "",
                    "tenant_id": tid,
                    "updated_at": float(item.get("updated_at") or 0.0),
                }
            )
    return rows


def search_models(
    *,
    manager: Any,
    query: str = "",
    source: str = "",
    model_type: str = "",
    installed: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    tenant_id: Optional[str] = None,
    norm_source: Callable[[str], str],
    norm_model_type: Callable[[str], str],
    default_tenant_id: str,
) -> Dict[str, Any]:
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    q = str(query or "").strip().lower()
    src_filter = norm_source(source) if str(source or "").strip() else ""
    type_filter = norm_model_type(model_type) if str(model_type or "").strip() else ""
    rows = collect_install_entries(
        manager=manager,
        tenant_id=tenant_id,
        default_tenant_id=default_tenant_id,
        norm_model_type=norm_model_type,
        norm_source=norm_source,
    ) + collect_catalog_entries(
        manager=manager,
        tenant_id=tenant_id,
        default_tenant_id=default_tenant_id,
        norm_model_type=norm_model_type,
        norm_source=norm_source,
    )
    out = []
    for row in rows:
        if src_filter and row["source"] != src_filter:
            continue
        if type_filter and row["model_type"] != type_filter:
            continue
        if installed is not None and bool(row["installed"]) != bool(installed):
            continue
        if q:
            hay = " ".join(
                [
                    str(row["id"]).lower(),
                    str(row["name"]).lower(),
                    " ".join(str(x).lower() for x in (row.get("tags") or [])),
                ]
            )
            if q not in hay:
                continue
        out.append(row)
    # IMPORTANT: deterministic order is part of the search contract.
    out.sort(
        key=lambda row: (
            0 if row["installed"] else 1,
            str(row["name"]).lower(),
            str(row["id"]).lower(),
            str(row["source"]).lower(),
        )
    )
    total = len(out)
    page = out[offset : offset + limit]
    return {
        "items": page,
        "pagination": {"limit": limit, "offset": offset, "total": total},
        "filters": {
            "query": q,
            "source": src_filter or None,
            "model_type": type_filter or None,
            "installed": installed,
        },
    }


def list_installations(
    *,
    manager: Any,
    tenant_id: Optional[str] = None,
    model_type: str = "",
    limit: int = 100,
    offset: int = 0,
    norm_model_type: Callable[[str], str],
    default_tenant_id: str,
) -> Dict[str, Any]:
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    type_filter = norm_model_type(model_type) if str(model_type or "").strip() else ""
    rows = []
    for rec in load_installations(installations_path=manager.installations_path):
        if not manager._tenant_ok(
            str(rec.get("tenant_id") or default_tenant_id), tenant_id
        ):
            continue
        if (
            type_filter
            and norm_model_type(str(rec.get("model_type") or "")) != type_filter
        ):
            continue
        rows.append(rec)
    rows.sort(key=lambda x: float(x.get("installed_at") or 0.0), reverse=True)
    total = len(rows)
    return {
        "installations": rows[offset : offset + limit],
        "pagination": {"limit": limit, "offset": offset, "total": total},
        "filters": {"model_type": type_filter or None},
    }
