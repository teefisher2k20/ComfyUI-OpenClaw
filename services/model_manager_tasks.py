"""
Internal task persistence/recovery helpers for the model manager facade.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional


def persist_tasks_locked(
    *,
    manager: Any,
    force: bool = False,
    atomic_json_write: Callable[[Path, Any], None],
) -> None:
    now = time.time()
    if not force and (now - manager._last_tasks_persist_at) < 0.3:
        return
    rows = [task.to_dict() for task in manager._tasks.values()]
    rows.sort(key=lambda row: float(row.get("created_at") or 0.0))
    atomic_json_write(manager.tasks_path, rows)
    manager._last_tasks_persist_at = now


def load_tasks_from_disk(
    *,
    manager: Any,
    task_from_dict: Callable[[Dict[str, Any]], Any],
    logger: Any,
) -> None:
    if not manager.tasks_path.exists():
        return
    try:
        data = json.loads(manager.tasks_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(
            "F65: failed to parse download task state, ignoring", exc_info=True
        )
        return
    if not isinstance(data, list):
        return
    with manager._lock:
        for item in data:
            try:
                task = task_from_dict(item)
            except Exception:
                continue
            if not task.task_id:
                continue
            manager._tasks[task.task_id] = task
            manager._task_change_seq = max(
                int(getattr(manager, "_task_change_seq", 0)),
                int(getattr(task, "change_seq", 0)),
            )
            if not task.is_terminal():
                manager._cancel_events[task.task_id] = (
                    manager._threading_event_factory()
                )


def recover_incomplete_tasks(*, manager: Any) -> None:
    now = time.time()
    with manager._lock:
        recoverable = sorted(
            [t for t in manager._tasks.values() if not t.is_terminal()],
            key=lambda t: t.created_at,
        )
        if not recoverable:
            return
        for task in recoverable:
            task.state = "recovering"
            task.updated_at = now
            task.error = "restart_recovery_pending"
            task.recovery_attempts += 1
            task.resume_status = "restart_recovering"
            manager._bump_task_change_seq_locked(task)
        replayable = recoverable[: manager.recovery_replay_limit]
        overflow = recoverable[manager.recovery_replay_limit :]
        for task in overflow:
            task.state = "failed"
            task.error = "recovery_replay_limit_exceeded"
            task.resume_status = "recovery_replay_limit_exceeded"
            task.finished_at = now
            task.updated_at = now
            manager._bump_task_change_seq_locked(task)
            manager._emit(task)
        for task in replayable:
            event = manager._cancel_events.setdefault(
                task.task_id, manager._threading_event_factory()
            )
            event.clear()
            task.state = "queued"
            task.cancel_requested = False
            task.error = ""
            task.updated_at = now
            task.resume_status = "restart_replay_queued"
            manager._bump_task_change_seq_locked(task)
            manager._futures[task.task_id] = manager._executor.submit(
                manager._run_task, task.task_id
            )
            manager._emit(task)
        manager._persist_tasks_locked(force=True)


def checkpoint_path(*, part_path: Path, checkpoint_suffix: str) -> Path:
    return Path(f"{part_path}{checkpoint_suffix}")


def load_checkpoint(*, checkpoint_path: Path) -> Dict[str, Any]:
    if not checkpoint_path.exists():
        return {}
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def checkpoint_matches_task(
    *,
    task: Any,
    checkpoint: Dict[str, Any],
    partial_bytes: int,
    checkpoint_version: int,
) -> bool:
    if not checkpoint:
        return False
    if int(checkpoint.get("version") or -1) != checkpoint_version:
        return False
    if str(checkpoint.get("task_id") or "") != task.task_id:
        return False
    if str(checkpoint.get("download_url") or "") != task.download_url:
        return False
    if str(checkpoint.get("expected_sha256") or "") != task.expected_sha256:
        return False
    if str(checkpoint.get("filename") or "") != task.filename:
        return False
    if int(checkpoint.get("bytes_downloaded") or -1) != int(partial_bytes):
        return False
    return True


def validators_match(
    *,
    checkpoint: Dict[str, Any],
    response_etag: str,
    response_last_modified: str,
) -> bool:
    expected_etag = str(checkpoint.get("etag") or "").strip()
    expected_last_modified = str(checkpoint.get("last_modified") or "").strip()
    if expected_etag and response_etag and expected_etag != response_etag:
        return False
    if (
        expected_last_modified
        and response_last_modified
        and expected_last_modified != response_last_modified
    ):
        return False
    return True


def save_checkpoint(
    *,
    manager: Any,
    checkpoint_path: Path,
    task: Any,
    bytes_downloaded: int,
    total_bytes: int,
    etag: str,
    last_modified: str,
    checkpoint_version: int,
    atomic_json_write: Callable[[Path, Any], None],
) -> None:
    payload = {
        "version": checkpoint_version,
        "task_id": task.task_id,
        "download_url": task.download_url,
        "expected_sha256": task.expected_sha256,
        "filename": task.filename,
        "bytes_downloaded": max(0, int(bytes_downloaded)),
        "total_bytes": max(0, int(total_bytes)),
        "etag": str(etag or ""),
        "last_modified": str(last_modified or ""),
        "updated_at": time.time(),
    }
    atomic_json_write(checkpoint_path, payload)
    with manager._lock:
        current = manager._tasks.get(task.task_id)
        if current is not None:
            current.last_checkpoint_at = payload["updated_at"]
            manager._persist_tasks_locked(force=False)


def set_resume_status(*, manager: Any, task_id: str, status: str) -> None:
    with manager._lock:
        current = manager._tasks.get(task_id)
        if current is None:
            return
        current.resume_status = str(status or "not_started")[:120]
        current.updated_at = time.time()
        manager._bump_task_change_seq_locked(current)
        manager._persist_tasks_locked(force=True)


def tenant_ok(
    *,
    record_tenant: str,
    request_tenant: Optional[str],
    default_tenant_id: str,
    is_multi_tenant_enabled: Callable[[], bool],
    normalize_tenant_id: Callable[[str], str],
) -> bool:
    if not is_multi_tenant_enabled():
        return True
    try:
        expect = normalize_tenant_id(request_tenant or default_tenant_id)
    except Exception:
        expect = default_tenant_id
    try:
        got = normalize_tenant_id(record_tenant or default_tenant_id)
    except Exception:
        got = default_tenant_id
    return got == expect


def emit(
    *,
    task: Any,
    event_type_cls: Any,
    event_store_getter: Callable[[], Any],
) -> None:
    event_type = {
        "queued": event_type_cls.QUEUED,
        "running": event_type_cls.RUNNING,
        "completed": event_type_cls.COMPLETED,
        "failed": event_type_cls.FAILED,
        "cancelled": event_type_cls.CANCELLED,
    }.get(task.state)
    if event_type is None:
        return
    event_store_getter().emit(
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
            "resume_status": task.resume_status,
        },
    )


def progress(*, manager: Any, task_id: str, downloaded: int, total: int) -> None:
    with manager._lock:
        task = manager._tasks.get(task_id)
        if task is None:
            return
        task.bytes_downloaded = max(0, int(downloaded))
        task.total_bytes = max(0, int(total))
        task.progress = (
            min(1.0, (task.bytes_downloaded / task.total_bytes))
            if task.total_bytes
            else 0.0
        )
        task.updated_at = time.time()
        manager._bump_task_change_seq_locked(task)
        manager._emit(task)
        manager._persist_tasks_locked(force=False)


def list_download_tasks(
    *,
    manager: Any,
    tenant_id: Optional[str] = None,
    state: str = "",
    limit: int = 100,
    offset: int = 0,
    since_seq: Optional[int] = None,
) -> Dict[str, Any]:
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    state_filter = str(state or "").strip().lower()
    with manager._lock:
        tasks = list(manager._tasks.values())
        latest_change_seq = int(getattr(manager, "_task_change_seq", 0))
    out = []
    for task in tasks:
        if not manager._tenant_ok(task.tenant_id, tenant_id):
            continue
        if state_filter and task.state != state_filter:
            continue
        out.append(task)
    if since_seq is None:
        out.sort(key=lambda x: x.created_at, reverse=True)
        total = len(out)
        page = [item.to_dict() for item in out[offset : offset + limit]]
        return {
            "tasks": page,
            "pagination": {"limit": limit, "offset": offset, "total": total},
            "filters": {"state": state_filter or None},
        }

    requested_since_seq = max(0, int(since_seq))
    effective_since_seq = requested_since_seq
    cursor_status = "ok"
    if requested_since_seq > latest_change_seq:
        cursor_status = "future_cursor_reset"
        effective_since_seq = latest_change_seq

    available_change_seqs = sorted(
        int(getattr(task, "change_seq", 0))
        for task in out
        if int(getattr(task, "change_seq", 0)) > 0
    )
    earliest_available_seq = available_change_seqs[0] if available_change_seqs else None
    latest_available_seq = available_change_seqs[-1] if available_change_seqs else None
    if (
        earliest_available_seq is not None
        and effective_since_seq != 0
        and effective_since_seq < (earliest_available_seq - 1)
    ):
        cursor_status = "stale_cursor_reset"
        effective_since_seq = max(0, earliest_available_seq - 1)

    out = [
        task
        for task in out
        if int(getattr(task, "change_seq", 0)) > effective_since_seq
    ]
    out.sort(key=lambda x: (int(getattr(x, "change_seq", 0)), x.created_at))
    total = len(out)
    page_items = out[:limit]
    next_since_seq = (
        int(getattr(page_items[-1], "change_seq", 0))
        if page_items
        else effective_since_seq
    )
    truncated = bool(
        total > len(page_items)
        or (
            isinstance(latest_available_seq, int)
            and latest_available_seq > next_since_seq
        )
    )
    return {
        "tasks": [item.to_dict() for item in page_items],
        "pagination": {"limit": limit, "offset": 0, "total": total},
        "filters": {"state": state_filter or None},
        "delta": {
            "cursor_key": "since_seq",
            "requested_since_seq": requested_since_seq,
            "effective_since_seq": effective_since_seq,
            "next_since_seq": next_since_seq,
            "latest_change_seq": latest_change_seq,
            "earliest_available_seq": earliest_available_seq,
            "latest_available_seq": latest_available_seq,
            "cursor_status": cursor_status,
            "snapshot": False,
            "truncated": truncated,
            "warnings": [],
        },
    }


def get_download_task(
    *,
    manager: Any,
    task_id: str,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    with manager._lock:
        task = manager._tasks.get(task_id)
        if task is None or not manager._tenant_ok(task.tenant_id, tenant_id):
            raise manager._error("not_found", "download task not found", 404)
        return task.to_dict()


def cancel_download_task(
    *,
    manager: Any,
    task_id: str,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    with manager._lock:
        task = manager._tasks.get(task_id)
        future = manager._futures.get(task_id)
        event = manager._cancel_events.get(task_id)
        if (
            task is None
            or event is None
            or not manager._tenant_ok(task.tenant_id, tenant_id)
        ):
            raise manager._error("not_found", "download task not found", 404)
        if task.is_terminal():
            return task.to_dict()
        task.cancel_requested = True
        task.updated_at = time.time()
        manager._bump_task_change_seq_locked(task)
        event.set()
        if task.state == "queued" and future is not None and future.cancel():
            task.state = "cancelled"
            task.error = "cancelled_before_start"
            task.finished_at = time.time()
            task.updated_at = task.finished_at
            manager._bump_task_change_seq_locked(task)
        manager._emit(task)
        manager._persist_tasks_locked(force=True)
        return task.to_dict()
