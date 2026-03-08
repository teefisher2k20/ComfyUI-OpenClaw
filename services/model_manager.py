"""
F54 model search/download/import service.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .job_events import JobEventType, get_job_event_store
from .safe_io import (
    STANDARD_OUTBOUND_POLICY,
    SSRFError,
    _build_pinned_opener,
    resolve_under_root,
    validate_outbound_url,
)
from .state_dir import get_state_dir
from .tenant_context import DEFAULT_TENANT_ID, is_multi_tenant_enabled, normalize_tenant_id

logger = logging.getLogger("ComfyUI-OpenClaw.services.model_manager")

STATE_SUBDIR = "model_manager"
CATALOG_SUBDIR = "catalog"
STAGING_SUBDIR = "staging"
INSTALLATIONS_FILE = "installations.json"
DEFAULT_MODEL_TYPE = "checkpoint"
MODEL_TYPE_TO_SUBDIR = {
    "checkpoint": "checkpoints",
    "lora": "loras",
    "vae": "vae",
    "controlnet": "controlnet",
    "embedding": "embeddings",
}
ALLOWED_MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx"}


class ModelManagerError(ValueError):
    def __init__(self, code: str, detail: str, status: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = int(status)


class DownloadCancelled(RuntimeError):
    pass


@dataclass
class DownloadTask:
    task_id: str
    model_id: str
    name: str
    model_type: str
    source: str
    source_label: str
    download_url: str
    destination_subdir: str
    filename: str
    expected_sha256: str
    provenance: Dict[str, Any]
    tenant_id: str
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    bytes_downloaded: int = 0
    total_bytes: int = 0
    progress: float = 0.0
    cancel_requested: bool = False
    error: str = ""
    staged_path: str = ""
    computed_sha256: str = ""
    imported: bool = False
    installation_path: str = ""
    installation_record_id: str = ""

    def is_terminal(self) -> bool:
        return self.state in {"completed", "failed", "cancelled"}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "model_id": self.model_id,
            "name": self.name,
            "model_type": self.model_type,
            "source": self.source,
            "source_label": self.source_label,
            "download_url": self.download_url,
            "destination_subdir": self.destination_subdir,
            "filename": self.filename,
            "expected_sha256": self.expected_sha256,
            "provenance": dict(self.provenance),
            "tenant_id": self.tenant_id,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "bytes_downloaded": self.bytes_downloaded,
            "total_bytes": self.total_bytes,
            "progress": self.progress,
            "cancel_requested": self.cancel_requested,
            "error": self.error,
            "staged_path": self.staged_path,
            "computed_sha256": self.computed_sha256,
            "imported": self.imported,
            "installation_path": self.installation_path,
            "installation_record_id": self.installation_record_id,
        }


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _norm_model_type(model_type: str) -> str:
    text = str(model_type or "").strip().lower()
    if not text:
        return DEFAULT_MODEL_TYPE
    return text if text in MODEL_TYPE_TO_SUBDIR else "other"


def _norm_source(source: str) -> str:
    out = "".join(
        ch for ch in str(source or "unknown").strip().lower() if ch.isalnum() or ch in {"_", "-", "."}
    )
    return out[:48] or "unknown"


def _parse_hosts(raw: str) -> set[str]:
    out: set[str] = set()
    for item in str(raw or "").replace(";", ",").split(","):
        host = item.strip().lower().rstrip(".")
        if host:
            out.add(host)
    return out


def _is_sha256(value: str) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def _sanitize_subdir(text: str) -> str:
    parts = [p for p in str(text or "").replace("\\", "/").split("/") if p not in {"", ".", ".."}]
    if not parts:
        raise ModelManagerError("invalid_destination", "destination_subdir is required")
    cleaned = []
    for part in parts:
        token = "".join(ch for ch in part if ch.isalnum() or ch in {"_", "-", "."})
        if not token:
            raise ModelManagerError("invalid_destination", f"invalid destination segment: {part!r}")
        cleaned.append(token)
    return "/".join(cleaned)


def _sanitize_filename(text: str) -> str:
    clean = str(text or "").strip().replace("\\", "_").replace("/", "_").replace(" ", "_")
    clean = "".join(ch for ch in clean if ch.isalnum() or ch in {"_", "-", ".", "(", ")", "[", "]"})
    if not clean or clean in {".", ".."}:
        raise ModelManagerError("invalid_filename", "filename is invalid")
    ext = Path(clean).suffix.lower()
    if ext not in ALLOWED_MODEL_EXTENSIONS:
        raise ModelManagerError(
            "invalid_filename",
            "filename extension must be one of " + ", ".join(sorted(ALLOWED_MODEL_EXTENSIONS)),
        )
    return clean[:180]


def _filename_from_url(url: str) -> str:
    seg = (urlparse(url).path or "").rstrip("/").split("/")[-1]
    if not seg:
        raise ModelManagerError("invalid_filename", "URL has no terminal filename")
    return _sanitize_filename(seg)


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f"{path.name}.tmp.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    except Exception:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class ModelManager:
    def __init__(self, *, state_root: Optional[Path] = None, install_root: Optional[Path] = None):
        self.state_root = Path(state_root or (Path(get_state_dir()) / STATE_SUBDIR))
        self.catalog_dir = self.state_root / CATALOG_SUBDIR
        self.staging_dir = self.state_root / STAGING_SUBDIR
        self.installations_path = self.state_root / INSTALLATIONS_FILE
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.catalog_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        root_env = (os.environ.get("OPENCLAW_MODEL_INSTALL_ROOT") or os.environ.get("MOLTBOT_MODEL_INSTALL_ROOT") or "").strip()
        # CRITICAL: keep explicit branch order. A compact inline ternary here can
        # accidentally ignore injected test/runtime install_root overrides.
        if install_root is not None:
            resolved_install_root = Path(install_root)
        elif root_env:
            resolved_install_root = Path(os.path.abspath(root_env))
        else:
            resolved_install_root = Path(get_state_dir()) / "models"
        self.install_root = resolved_install_root
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.allow_hosts = _parse_hosts(
            os.environ.get("OPENCLAW_MODEL_DOWNLOAD_ALLOW_HOSTS")
            or os.environ.get("MOLTBOT_MODEL_DOWNLOAD_ALLOW_HOSTS")
            or ""
        )
        self.allow_any_public = _truthy(
            os.environ.get("OPENCLAW_MODEL_DOWNLOAD_ALLOW_ANY_PUBLIC")
            or os.environ.get("MOLTBOT_MODEL_DOWNLOAD_ALLOW_ANY_PUBLIC")
            or "0"
        )
        self.allow_loopback_hosts = _parse_hosts(
            os.environ.get("OPENCLAW_MODEL_DOWNLOAD_ALLOW_LOOPBACK_HOSTS")
            or os.environ.get("MOLTBOT_MODEL_DOWNLOAD_ALLOW_LOOPBACK_HOSTS")
            or ""
        )
        self.max_workers = self._read_int(("OPENCLAW_MODEL_DOWNLOAD_MAX_CONCURRENCY", "MOLTBOT_MODEL_DOWNLOAD_MAX_CONCURRENCY"), 2, 1, 4)
        self.max_active = self._read_int(("OPENCLAW_MODEL_DOWNLOAD_MAX_ACTIVE", "MOLTBOT_MODEL_DOWNLOAD_MAX_ACTIVE"), 16, 1, 128)
        self.timeout_sec = self._read_int(("OPENCLAW_MODEL_DOWNLOAD_TIMEOUT_SEC", "MOLTBOT_MODEL_DOWNLOAD_TIMEOUT_SEC"), 120, 5, 3600)
        self._lock = threading.Lock()
        self._tasks: Dict[str, DownloadTask] = {}
        self._futures: Dict[str, Future] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="openclaw-model-download")

    @staticmethod
    def _read_int(keys: tuple[str, ...], default: int, minimum: int, maximum: int) -> int:
        for key in keys:
            raw = os.environ.get(key)
            if raw is None or str(raw).strip() == "":
                continue
            try:
                val = int(str(raw).strip())
            except Exception:
                return default
            if val < minimum or val > maximum:
                return default
            return val
        return default

    def _tenant_ok(self, record_tenant: str, request_tenant: Optional[str]) -> bool:
        if not is_multi_tenant_enabled():
            return True
        try:
            expect = normalize_tenant_id(request_tenant or DEFAULT_TENANT_ID)
        except Exception:
            expect = DEFAULT_TENANT_ID
        try:
            got = normalize_tenant_id(record_tenant or DEFAULT_TENANT_ID)
        except Exception:
            got = DEFAULT_TENANT_ID
        return got == expect

    def _emit(self, task: DownloadTask) -> None:
        event_type = {
            "queued": JobEventType.QUEUED,
            "running": JobEventType.RUNNING,
            "completed": JobEventType.COMPLETED,
            "failed": JobEventType.FAILED,
            "cancelled": JobEventType.CANCELLED,
        }.get(task.state)
        if event_type is None:
            return
        get_job_event_store().emit(
            event_type=event_type,
            prompt_id=f"model_download:{task.task_id}",
            trace_id="",
            data={
                "channel": "model_download",
                "task_id": task.task_id,
                "model_id": task.model_id,
                "state": task.state,
                "progress": task.progress,
                "bytes_downloaded": task.bytes_downloaded,
                "total_bytes": task.total_bytes,
                "error": task.error,
                "source": task.source,
                "source_label": task.source_label,
            },
        )

    def _load_installations(self) -> List[Dict[str, Any]]:
        if not self.installations_path.exists():
            return []
        try:
            data = json.loads(self.installations_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _save_installations(self, rows: List[Dict[str, Any]]) -> None:
        _atomic_json_write(self.installations_path, rows)

    def _collect_install_entries(self, tenant_id: Optional[str]) -> List[Dict[str, Any]]:
        rows = []
        for rec in self._load_installations():
            if not self._tenant_ok(str(rec.get("tenant_id") or DEFAULT_TENANT_ID), tenant_id):
                continue
            rows.append(
                {
                    "id": str(rec.get("model_id") or rec.get("id") or ""),
                    "name": str(rec.get("name") or ""),
                    "model_type": _norm_model_type(str(rec.get("model_type") or "")),
                    "source": _norm_source(str(rec.get("source") or "managed_install")),
                    "source_label": str(rec.get("source_label") or "Managed Install"),
                    "installed": True,
                    "download_url": str(rec.get("download_url") or ""),
                    "sha256": str(rec.get("sha256") or "").lower(),
                    "size_bytes": rec.get("size_bytes"),
                    "tags": list(rec.get("tags") or []),
                    "provenance": dict(rec.get("provenance") or {}),
                    "installation_path": str(rec.get("installation_path") or ""),
                    "tenant_id": str(rec.get("tenant_id") or DEFAULT_TENANT_ID),
                    "updated_at": float(rec.get("installed_at") or rec.get("updated_at") or 0.0),
                }
            )
        return [row for row in rows if row["id"] and row["name"]]

    def _collect_catalog_entries(self, tenant_id: Optional[str]) -> List[Dict[str, Any]]:
        rows = []
        for path in sorted(self.catalog_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            src = _norm_source(str(payload.get("source") or path.stem))
            src_label = str(payload.get("source_label") or src)
            items = payload.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                tid = str(item.get("tenant_id") or DEFAULT_TENANT_ID)
                if not self._tenant_ok(tid, tenant_id):
                    continue
                model_id = str(item.get("id") or item.get("model_id") or "").strip()
                name = str(item.get("name") or model_id).strip()
                if not model_id or not name:
                    continue
                rows.append(
                    {
                        "id": model_id,
                        "name": name,
                        "model_type": _norm_model_type(str(item.get("model_type") or "")),
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
        self,
        *,
        query: str = "",
        source: str = "",
        model_type: str = "",
        installed: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        q = str(query or "").strip().lower()
        src_filter = _norm_source(source) if str(source or "").strip() else ""
        type_filter = _norm_model_type(model_type) if str(model_type or "").strip() else ""
        rows = self._collect_install_entries(tenant_id) + self._collect_catalog_entries(tenant_id)
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
        out.sort(key=lambda row: (0 if row["installed"] else 1, str(row["name"]).lower(), str(row["id"]).lower(), str(row["source"]).lower()))
        total = len(out)
        page = out[offset : offset + limit]
        return {
            "items": page,
            "pagination": {"limit": limit, "offset": offset, "total": total},
            "filters": {"query": q, "source": src_filter or None, "model_type": type_filter or None, "installed": installed},
        }

    def _validate_url_policy(self, url: str) -> None:
        if not str(url or "").strip():
            raise ModelManagerError("invalid_url", "download_url is required")
        if not self.allow_hosts and not self.allow_any_public:
            raise ModelManagerError(
                "download_host_policy_missing",
                "set OPENCLAW_MODEL_DOWNLOAD_ALLOW_HOSTS or OPENCLAW_MODEL_DOWNLOAD_ALLOW_ANY_PUBLIC=1",
            )
        try:
            validate_outbound_url(
                str(url).strip(),
                allow_hosts=self.allow_hosts or None,
                allow_any_public_host=self.allow_any_public,
                allow_loopback_hosts=self.allow_loopback_hosts or None,
                policy=STANDARD_OUTBOUND_POLICY,
            )
        except SSRFError as exc:
            raise ModelManagerError("ssrf_blocked", str(exc))

    @staticmethod
    def _validate_provenance(provenance: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(provenance, dict):
            raise ModelManagerError("invalid_provenance", "provenance must be an object")
        out = {
            "publisher": str(provenance.get("publisher") or "").strip(),
            "license": str(provenance.get("license") or "").strip(),
            "source_url": str(provenance.get("source_url") or "").strip(),
            "note": str(provenance.get("note") or "").strip()[:500],
        }
        if not out["publisher"] or not out["license"] or not out["source_url"]:
            raise ModelManagerError(
                "invalid_provenance",
                "provenance.publisher, provenance.license, provenance.source_url are required",
            )
        return out

    def _normalize_tenant(self, tenant_id: Optional[str]) -> str:
        if not is_multi_tenant_enabled():
            return DEFAULT_TENANT_ID
        return normalize_tenant_id(tenant_id or DEFAULT_TENANT_ID)

    def _assert_budget(self) -> None:
        active = 0
        with self._lock:
            for task in self._tasks.values():
                if task.state in {"queued", "running"}:
                    active += 1
        if active >= self.max_active:
            raise ModelManagerError("download_queue_full", f"download queue full (limit={self.max_active})", 429)

    def create_download_task(
        self,
        *,
        model_id: str,
        name: str,
        model_type: str,
        source: str,
        source_label: str,
        download_url: str,
        expected_sha256: str,
        provenance: Dict[str, Any],
        destination_subdir: Optional[str] = None,
        filename: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._assert_budget()
        model_id = str(model_id or "").strip()
        if not model_id:
            raise ModelManagerError("validation_error", "model_id is required")
        name = str(name or "").strip()
        if not name:
            raise ModelManagerError("validation_error", "name is required")
        digest = str(expected_sha256 or "").strip().lower()
        if not _is_sha256(digest):
            raise ModelManagerError("validation_error", "expected_sha256 must be a 64-char hex string")
        self._validate_url_policy(download_url)
        provenance = self._validate_provenance(provenance)
        mtype = _norm_model_type(model_type)
        dest_subdir = _sanitize_subdir(destination_subdir or MODEL_TYPE_TO_SUBDIR.get(mtype, "misc"))
        fname = _sanitize_filename(filename) if filename else _filename_from_url(download_url)
        task = DownloadTask(
            task_id=str(uuid.uuid4()),
            model_id=model_id,
            name=name,
            model_type=mtype,
            source=_norm_source(source),
            source_label=str(source_label or _norm_source(source))[:80],
            download_url=str(download_url).strip(),
            destination_subdir=dest_subdir,
            filename=fname,
            expected_sha256=digest,
            provenance=provenance,
            tenant_id=self._normalize_tenant(tenant_id),
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._cancel_events[task.task_id] = threading.Event()
            self._futures[task.task_id] = self._executor.submit(self._run_task, task.task_id)
        self._emit(task)
        return task.to_dict()

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            cancel_event = self._cancel_events.get(task_id)
            if task is None or cancel_event is None:
                return
            task.state = "running"
            task.started_at = time.time()
            task.updated_at = task.started_at
            self._emit(task)
        try:
            staged_path, digest = self._download(task, cancel_event)
            with self._lock:
                current = self._tasks.get(task_id)
                if current is None:
                    return
                current.state = "completed"
                current.updated_at = time.time()
                current.finished_at = current.updated_at
                current.progress = 1.0
                current.staged_path = staged_path
                current.computed_sha256 = digest
                self._emit(current)
        except DownloadCancelled:
            with self._lock:
                current = self._tasks.get(task_id)
                if current is None:
                    return
                current.state = "cancelled"
                current.error = "cancelled"
                current.updated_at = time.time()
                current.finished_at = current.updated_at
                self._emit(current)
        except Exception as exc:
            with self._lock:
                current = self._tasks.get(task_id)
                if current is None:
                    return
                current.state = "failed"
                current.error = str(exc)
                current.updated_at = time.time()
                current.finished_at = current.updated_at
                self._emit(current)

    def _download(self, task: DownloadTask, cancel_event: threading.Event) -> tuple[str, str]:
        _scheme, _host, _port, pinned_ips = validate_outbound_url(
            task.download_url,
            allow_hosts=self.allow_hosts or None,
            allow_any_public_host=self.allow_any_public,
            allow_loopback_hosts=self.allow_loopback_hosts or None,
            policy=STANDARD_OUTBOUND_POLICY,
        )
        opener = _build_pinned_opener(pinned_ips)
        req = urllib.request.Request(task.download_url, method="GET")
        req.add_header("User-Agent", "ComfyUI-OpenClaw/F54")
        stage_dir = self.staging_dir / task.task_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        part = stage_dir / f"{task.filename}.part"
        final = stage_dir / task.filename
        for p in (part, final):
            if p.exists():
                p.unlink()
        digest = hashlib.sha256()
        downloaded = 0
        total = 0
        last_emit = 0.0
        with opener.open(req, timeout=self.timeout_sec) as resp:
            code = int(resp.getcode() or 0)
            if code in (301, 302, 303, 307, 308):
                raise ModelManagerError("download_redirect_blocked", "redirect blocked for managed downloads")
            if code >= 400:
                raise ModelManagerError("download_http_error", f"HTTP {code}")
            try:
                total = max(0, int(str(resp.headers.get("Content-Length") or "0")))
            except Exception:
                total = 0
            with open(part, "wb") as fh:
                while True:
                    if cancel_event.is_set():
                        raise DownloadCancelled()
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_emit >= 0.35:
                        self._progress(task.task_id, downloaded, total)
                        last_emit = now
        got = digest.hexdigest()
        if got != task.expected_sha256:
            try:
                part.unlink(missing_ok=True)  # type: ignore[attr-defined]
            except Exception:
                pass
            raise ModelManagerError("sha256_mismatch", f"expected {task.expected_sha256}, got {got}")
        os.replace(part, final)
        self._progress(task.task_id, downloaded, total or downloaded)
        return str(final), got

    def _progress(self, task_id: str, downloaded: int, total: int) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.bytes_downloaded = max(0, int(downloaded))
            task.total_bytes = max(0, int(total))
            task.progress = min(1.0, (task.bytes_downloaded / task.total_bytes)) if task.total_bytes else 0.0
            task.updated_at = time.time()
            self._emit(task)

    def list_download_tasks(
        self,
        *,
        tenant_id: Optional[str] = None,
        state: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        state_filter = str(state or "").strip().lower()
        with self._lock:
            tasks = list(self._tasks.values())
        out = []
        for task in tasks:
            if not self._tenant_ok(task.tenant_id, tenant_id):
                continue
            if state_filter and task.state != state_filter:
                continue
            out.append(task)
        out.sort(key=lambda x: x.created_at, reverse=True)
        total = len(out)
        page = [item.to_dict() for item in out[offset : offset + limit]]
        return {
            "tasks": page,
            "pagination": {"limit": limit, "offset": offset, "total": total},
            "filters": {"state": state_filter or None},
        }

    def get_download_task(self, task_id: str, *, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or not self._tenant_ok(task.tenant_id, tenant_id):
                raise ModelManagerError("not_found", "download task not found", 404)
            return task.to_dict()

    def cancel_download_task(self, task_id: str, *, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            future = self._futures.get(task_id)
            event = self._cancel_events.get(task_id)
            if task is None or event is None or not self._tenant_ok(task.tenant_id, tenant_id):
                raise ModelManagerError("not_found", "download task not found", 404)
            if task.is_terminal():
                return task.to_dict()
            task.cancel_requested = True
            task.updated_at = time.time()
            event.set()
            if task.state == "queued" and future is not None and future.cancel():
                task.state = "cancelled"
                task.error = "cancelled_before_start"
                task.finished_at = time.time()
                task.updated_at = task.finished_at
            self._emit(task)
            return task.to_dict()

    def import_downloaded_model(
        self,
        *,
        task_id: str,
        tenant_id: Optional[str] = None,
        destination_subdir: Optional[str] = None,
        filename: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or not self._tenant_ok(task.tenant_id, tenant_id):
                raise ModelManagerError("not_found", "download task not found", 404)
            if task.state != "completed":
                raise ModelManagerError("task_not_ready", "task must be completed before import")
            if task.imported:
                raise ModelManagerError("already_imported", "task already imported")
            staged_path = Path(task.staged_path)
            expected = task.expected_sha256
            computed = task.computed_sha256
        if not staged_path.exists():
            raise ModelManagerError("staging_missing", "staged file missing")
        # CRITICAL: keep import-time hash verification. Removing this reopens
        # tamper window between download completion and activation/import.
        actual = _file_sha256(staged_path)
        if actual != expected or computed != expected:
            raise ModelManagerError("sha256_mismatch", f"expected {expected}, got {actual}")
        self._validate_provenance(task.provenance)
        subdir = _sanitize_subdir(destination_subdir or task.destination_subdir)
        fname = _sanitize_filename(filename or task.filename)
        rel_target = f"{subdir}/{fname}"
        # IMPORTANT: keep root-bounded resolution; plain joins re-enable traversal risks.
        abs_target = Path(resolve_under_root(str(self.install_root), rel_target))
        abs_target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{abs_target.name}.tmp.", dir=str(abs_target.parent), text=False)
        os.close(fd)
        try:
            shutil.copy2(staged_path, tmp)
            os.replace(tmp, abs_target)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        safe_tags: List[str] = []
        for item in tags or []:
            if not isinstance(item, str):
                continue
            clean = item.strip().lower()
            if not clean or clean in safe_tags:
                continue
            safe_tags.append(clean)
            if len(safe_tags) >= 24:
                break
        rec = {
            "id": str(uuid.uuid4()),
            "task_id": task.task_id,
            "model_id": task.model_id,
            "name": task.name,
            "model_type": task.model_type,
            "source": task.source,
            "source_label": task.source_label,
            "download_url": task.download_url,
            "sha256": expected,
            "size_bytes": abs_target.stat().st_size if abs_target.exists() else None,
            "provenance": dict(task.provenance),
            "installation_path": rel_target.replace("\\", "/"),
            "tenant_id": task.tenant_id,
            "installed_at": time.time(),
            "tags": safe_tags,
        }
        rows = self._load_installations()
        rows.append(rec)
        rows.sort(key=lambda x: float(x.get("installed_at") or 0.0), reverse=True)
        self._save_installations(rows)
        with self._lock:
            current = self._tasks.get(task.task_id)
            if current is not None:
                current.imported = True
                current.installation_path = rec["installation_path"]
                current.installation_record_id = rec["id"]
                current.updated_at = time.time()
                self._emit(current)
        return rec

    def list_installations(
        self,
        *,
        tenant_id: Optional[str] = None,
        model_type: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        type_filter = _norm_model_type(model_type) if str(model_type or "").strip() else ""
        rows = []
        for rec in self._load_installations():
            if not self._tenant_ok(str(rec.get("tenant_id") or DEFAULT_TENANT_ID), tenant_id):
                continue
            if type_filter and _norm_model_type(str(rec.get("model_type") or "")) != type_filter:
                continue
            rows.append(rec)
        rows.sort(key=lambda x: float(x.get("installed_at") or 0.0), reverse=True)
        total = len(rows)
        return {
            "installations": rows[offset : offset + limit],
            "pagination": {"limit": limit, "offset": offset, "total": total},
            "filters": {"model_type": type_filter or None},
        }


model_manager = ModelManager()
