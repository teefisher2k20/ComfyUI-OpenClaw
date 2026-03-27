"""
Internal download/import lifecycle helpers for the model manager facade.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def validate_url_policy(*, manager: Any, url: str) -> None:
    if not str(url or "").strip():
        raise manager._error("invalid_url", "download_url is required")
    if not manager.allow_hosts and not manager.allow_any_public:
        raise manager._error(
            "download_host_policy_missing",
            "set OPENCLAW_MODEL_DOWNLOAD_ALLOW_HOSTS or OPENCLAW_MODEL_DOWNLOAD_ALLOW_ANY_PUBLIC=1",
        )
    try:
        manager._validate_outbound_download_url(
            str(url).strip(),
            allow_hosts=manager.allow_hosts or None,
            allow_any_public_host=manager.allow_any_public,
            allow_loopback_hosts=manager.allow_loopback_hosts or None,
        )
    except manager._ssrf_error_cls as exc:
        raise manager._error("ssrf_blocked", str(exc))


def validate_provenance(*, manager: Any, provenance: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(provenance, dict):
        raise manager._error("invalid_provenance", "provenance must be an object")
    out = {
        "publisher": str(provenance.get("publisher") or "").strip(),
        "license": str(provenance.get("license") or "").strip(),
        "source_url": str(provenance.get("source_url") or "").strip(),
        "note": str(provenance.get("note") or "").strip()[:500],
    }
    if not out["publisher"] or not out["license"] or not out["source_url"]:
        raise manager._error(
            "invalid_provenance",
            "provenance.publisher, provenance.license, provenance.source_url are required",
        )
    return out


def normalize_tenant(*, manager: Any, tenant_id: Optional[str]) -> str:
    if not manager._is_multi_tenant_enabled():
        return manager._default_tenant_id
    return manager._normalize_tenant_id(tenant_id or manager._default_tenant_id)


def assert_budget(*, manager: Any) -> None:
    active = 0
    with manager._lock:
        for task in manager._tasks.values():
            if task.state in {"queued", "running"}:
                active += 1
    if active >= manager.max_active:
        raise manager._error(
            "download_queue_full",
            f"download queue full (limit={manager.max_active})",
            429,
        )


def create_download_task(
    *,
    manager: Any,
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
    manager._assert_budget()
    model_id = str(model_id or "").strip()
    if not model_id:
        raise manager._error("validation_error", "model_id is required")
    name = str(name or "").strip()
    if not name:
        raise manager._error("validation_error", "name is required")
    digest = str(expected_sha256 or "").strip().lower()
    if not manager._is_sha256(digest):
        raise manager._error(
            "validation_error", "expected_sha256 must be a 64-char hex string"
        )
    manager._validate_url_policy(download_url)
    provenance = manager._validate_provenance(provenance)
    mtype = manager._norm_model_type(model_type)
    dest_subdir = manager._sanitize_subdir(
        destination_subdir or manager._model_type_to_subdir.get(mtype, "misc")
    )
    fname = (
        manager._sanitize_filename(filename)
        if filename
        else manager._filename_from_url(download_url)
    )
    task = manager._download_task_cls(
        task_id=str(uuid.uuid4()),
        model_id=model_id,
        name=name,
        model_type=mtype,
        source=manager._norm_source(source),
        source_label=str(source_label or manager._norm_source(source))[:80],
        download_url=str(download_url).strip(),
        destination_subdir=dest_subdir,
        filename=fname,
        expected_sha256=digest,
        provenance=provenance,
        tenant_id=manager._normalize_tenant(tenant_id),
        resume_status="queued_new",
    )
    with manager._lock:
        manager._tasks[task.task_id] = task
        manager._bump_task_change_seq_locked(task)
        manager._cancel_events[task.task_id] = manager._threading_event_factory()
        manager._futures[task.task_id] = manager._executor.submit(
            manager._run_task, task.task_id
        )
        manager._persist_tasks_locked(force=True)
    manager._emit(task)
    return task.to_dict()


def run_task(*, manager: Any, task_id: str) -> None:
    with manager._lock:
        task = manager._tasks.get(task_id)
        cancel_event = manager._cancel_events.get(task_id)
        if task is None or cancel_event is None:
            return
        task.state = "running"
        task.started_at = time.time()
        task.updated_at = task.started_at
        task.resume_status = task.resume_status or "running"
        manager._bump_task_change_seq_locked(task)
        manager._emit(task)
        manager._persist_tasks_locked(force=True)
    try:
        staged_path, digest = manager._download(task, cancel_event)
        with manager._lock:
            current = manager._tasks.get(task_id)
            if current is None:
                return
            current.state = "completed"
            current.updated_at = time.time()
            current.finished_at = current.updated_at
            current.progress = 1.0
            current.staged_path = staged_path
            current.computed_sha256 = digest
            manager._bump_task_change_seq_locked(current)
            manager._emit(current)
            manager._persist_tasks_locked(force=True)
    except manager._download_cancelled_cls:
        with manager._lock:
            current = manager._tasks.get(task_id)
            if current is None:
                return
            current.state = "cancelled"
            current.error = "cancelled"
            current.updated_at = time.time()
            current.finished_at = current.updated_at
            manager._bump_task_change_seq_locked(current)
            manager._emit(current)
            manager._persist_tasks_locked(force=True)
    except Exception as exc:
        with manager._lock:
            current = manager._tasks.get(task_id)
            if current is None:
                return
            current.state = "failed"
            current.error = str(exc)
            current.updated_at = time.time()
            current.finished_at = current.updated_at
            manager._bump_task_change_seq_locked(current)
            manager._emit(current)
            manager._persist_tasks_locked(force=True)


def download(*, manager: Any, task: Any, cancel_event: Any) -> tuple[str, str]:
    _scheme, _host, _port, pinned_ips = manager._validate_outbound_download_url(
        task.download_url,
        allow_hosts=manager.allow_hosts or None,
        allow_any_public_host=manager.allow_any_public,
        allow_loopback_hosts=manager.allow_loopback_hosts or None,
    )
    opener = manager._build_pinned_download_opener(pinned_ips)
    stage_dir = manager.staging_dir / task.task_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    part = stage_dir / f"{task.filename}.part"
    checkpoint = manager._checkpoint_path(part)
    final = stage_dir / task.filename
    if final.exists():
        manager._safe_unlink(final)

    resume_bytes = part.stat().st_size if part.exists() else 0
    checkpoint_data = manager._load_checkpoint(checkpoint)

    if resume_bytes > 0 and manager._checkpoint_matches_task(
        task, checkpoint_data, resume_bytes
    ):
        digest = hashlib.sha256()
        with open(part, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        manager._set_resume_status(task.task_id, "resume_attempt")
        downloaded, total, _etag, _last_modified, fallback_reason = (
            manager._stream_response_to_part(
                opener=opener,
                task=task,
                cancel_event=cancel_event,
                part=part,
                checkpoint=checkpoint,
                digest=digest,
                resume_from=resume_bytes,
                checkpoint_data=checkpoint_data,
            )
        )
        if not fallback_reason:
            got = digest.hexdigest()
            if got != task.expected_sha256:
                manager._safe_unlink(part)
                manager._safe_unlink(checkpoint)
                raise manager._error(
                    "sha256_mismatch", f"expected {task.expected_sha256}, got {got}"
                )
            os.replace(part, final)
            manager._safe_unlink(checkpoint)
            manager._set_resume_status(task.task_id, "resumed_partial")
            manager._progress(task.task_id, downloaded, total or downloaded)
            return str(final), got
        manager._set_resume_status(task.task_id, fallback_reason)
    elif resume_bytes > 0:
        # IMPORTANT: resume only when checkpoint metadata matches this task.
        # Blindly appending without metadata validation can corrupt artifacts.
        manager._set_resume_status(task.task_id, "resume_fallback_checkpoint_mismatch")

    manager._safe_unlink(part)
    manager._safe_unlink(checkpoint)
    digest = hashlib.sha256()
    downloaded, total, _etag, _last_modified, fallback_reason = (
        manager._stream_response_to_part(
            opener=opener,
            task=task,
            cancel_event=cancel_event,
            part=part,
            checkpoint=checkpoint,
            digest=digest,
            resume_from=0,
            checkpoint_data={},
        )
    )
    if fallback_reason:
        raise manager._error("download_resume_failed", fallback_reason)

    got = digest.hexdigest()
    if got != task.expected_sha256:
        manager._safe_unlink(part)
        manager._safe_unlink(checkpoint)
        raise manager._error(
            "sha256_mismatch", f"expected {task.expected_sha256}, got {got}"
        )
    os.replace(part, final)
    manager._safe_unlink(checkpoint)
    if resume_bytes <= 0:
        manager._set_resume_status(task.task_id, "started_fresh")
    manager._progress(task.task_id, downloaded, total or downloaded)
    return str(final), got


def stream_response_to_part(
    *,
    manager: Any,
    opener: Any,
    task: Any,
    cancel_event: Any,
    part: Path,
    checkpoint: Path,
    digest: Any,
    resume_from: int,
    checkpoint_data: Dict[str, Any],
) -> tuple[int, int, str, str, str]:
    req = urllib.request.Request(task.download_url, method="GET")
    req.add_header("User-Agent", "ComfyUI-OpenClaw/F65")
    if resume_from > 0:
        req.add_header("Range", f"bytes={resume_from}-")

    with opener.open(req, timeout=manager.timeout_sec) as resp:
        code = int(resp.getcode() or 0)
        if code in (301, 302, 303, 307, 308):
            raise manager._error(
                "download_redirect_blocked",
                "redirect blocked for managed downloads",
            )
        if code >= 400:
            raise manager._error("download_http_error", f"HTTP {code}")

        etag = str(resp.headers.get("ETag") or "").strip()
        last_modified = str(resp.headers.get("Last-Modified") or "").strip()
        content_length = 0
        try:
            content_length = max(0, int(str(resp.headers.get("Content-Length") or "0")))
        except Exception:
            content_length = 0

        if resume_from > 0:
            if code != 206:
                return (
                    resume_from,
                    0,
                    etag,
                    last_modified,
                    "resume_fallback_range_not_supported",
                )
            if not manager._validators_match(checkpoint_data, etag, last_modified):
                return (
                    resume_from,
                    0,
                    etag,
                    last_modified,
                    "resume_fallback_validator_mismatch",
                )
            range_start, _range_end, range_total = manager._parse_content_range(
                str(resp.headers.get("Content-Range") or "")
            )
            if range_start != resume_from:
                return (
                    resume_from,
                    0,
                    etag,
                    last_modified,
                    "resume_fallback_content_range_mismatch",
                )
            total = range_total if range_total > 0 else (resume_from + content_length)
            mode = "ab"
            downloaded = resume_from
        else:
            total = content_length
            mode = "wb"
            downloaded = 0

        last_emit = 0.0
        with open(part, mode) as fh:
            while True:
                if cancel_event.is_set():
                    manager._save_checkpoint(
                        checkpoint,
                        task,
                        bytes_downloaded=downloaded,
                        total_bytes=total,
                        etag=etag,
                        last_modified=last_modified,
                    )
                    raise manager._download_cancelled_cls()
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last_emit >= 0.35:
                    manager._progress(task.task_id, downloaded, total)
                    manager._save_checkpoint(
                        checkpoint,
                        task,
                        bytes_downloaded=downloaded,
                        total_bytes=total,
                        etag=etag,
                        last_modified=last_modified,
                    )
                    last_emit = now
        manager._progress(task.task_id, downloaded, total or downloaded)
        manager._save_checkpoint(
            checkpoint,
            task,
            bytes_downloaded=downloaded,
            total_bytes=total,
            etag=etag,
            last_modified=last_modified,
        )
        return downloaded, total, etag, last_modified, ""


def import_downloaded_model(
    *,
    manager: Any,
    task_id: str,
    tenant_id: Optional[str] = None,
    destination_subdir: Optional[str] = None,
    filename: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    with manager._lock:
        task = manager._tasks.get(task_id)
        if task is None or not manager._tenant_ok(task.tenant_id, tenant_id):
            raise manager._error("not_found", "download task not found", 404)
        if task.state != "completed":
            raise manager._error(
                "task_not_ready", "task must be completed before import"
            )
        if task.imported:
            raise manager._error("already_imported", "task already imported")
        staged_path = Path(task.staged_path)
        expected = task.expected_sha256
        computed = task.computed_sha256
    if not staged_path.exists():
        raise manager._error("staging_missing", "staged file missing")
    # CRITICAL: keep import-time hash verification. Removing this reopens
    # tamper window between download completion and activation/import.
    actual = manager._file_sha256(staged_path)
    if actual != expected or computed != expected:
        raise manager._error("sha256_mismatch", f"expected {expected}, got {actual}")
    manager._validate_provenance(task.provenance)
    subdir = manager._sanitize_subdir(destination_subdir or task.destination_subdir)
    fname = manager._sanitize_filename(filename or task.filename)
    rel_target = f"{subdir}/{fname}"
    # IMPORTANT: keep root-bounded resolution; plain joins re-enable traversal risks.
    abs_target = Path(
        manager._resolve_install_target(str(manager.install_root), rel_target)
    )
    abs_target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{abs_target.name}.tmp.", dir=str(abs_target.parent), text=False
    )
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
    rows = manager._load_installations()
    rows.append(rec)
    rows.sort(key=lambda x: float(x.get("installed_at") or 0.0), reverse=True)
    manager._save_installations(rows)
    with manager._lock:
        current = manager._tasks.get(task.task_id)
        if current is not None:
            current.imported = True
            current.installation_path = rec["installation_path"]
            current.installation_record_id = rec["id"]
            current.updated_at = time.time()
            manager._bump_task_change_seq_locked(current)
            manager._emit(current)
            manager._persist_tasks_locked(force=True)
    return rec
