"""
R99 Audit Service.
Standardized, append-only audit events for sensitive operations.
"""

import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from .redaction import redact_json, stable_redaction_tag

logger = logging.getLogger("ComfyUI-OpenClaw.services.audit")

_TRUTHY = {"1", "true", "yes", "on"}
_AUDIT_MAX_BYTES_DEFAULT = 5 * 1024 * 1024
_AUDIT_BACKUPS_DEFAULT = 3
_AUDIT_CHAIN_KEY: Optional[bytes] = None


def _default_audit_log_path() -> str:
    # Keep this lazy/fault-tolerant for unit tests that patch import topology.
    try:
        from .state_dir import get_state_dir
    except Exception:
        try:
            from services.state_dir import get_state_dir  # type: ignore
        except Exception:
            get_state_dir = None
    if get_state_dir:
        return os.path.join(get_state_dir(), "audit.log")
    return "audit.log"


AUDIT_LOG_PATH = (
    os.environ.get("OPENCLAW_AUDIT_LOG_PATH")
    or os.environ.get("MOLTBOT_AUDIT_LOG_PATH")
    or _default_audit_log_path()
)


def _env_int(primary: str, legacy: str, default: int) -> int:
    raw = os.environ.get(primary) or os.environ.get(legacy)
    if raw is None:
        return default
    try:
        value = int(raw)
        return value if value >= 0 else default
    except Exception:
        return default


def _audit_limits() -> Tuple[int, int]:
    max_bytes = _env_int(
        "OPENCLAW_AUDIT_MAX_BYTES",
        "MOLTBOT_AUDIT_MAX_BYTES",
        _AUDIT_MAX_BYTES_DEFAULT,
    )
    backups = _env_int(
        "OPENCLAW_AUDIT_MAX_BACKUPS",
        "MOLTBOT_AUDIT_MAX_BACKUPS",
        _AUDIT_BACKUPS_DEFAULT,
    )
    return max_bytes, backups


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            out[str(k)] = _json_safe(v)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _resolve_trace_id(details: Dict[str, Any]) -> str:
    if isinstance(details.get("trace_id"), str) and details.get("trace_id"):
        return details["trace_id"]
    # IMPORTANT: keep request headers out of audit persistence. CodeQL still treats
    # auth-bearing request objects as sensitive sources even when only trace headers
    # are read from them.
    return uuid.uuid4().hex


def _sanitize_audit_details(details: Optional[Dict[str, Any]]) -> Any:
    safe_details = _json_safe(details or {})
    if isinstance(safe_details, dict):
        sanitized = dict(safe_details)
        actor_ip = sanitized.pop("actor_ip", None)
        if actor_ip is not None:
            # IMPORTANT: keep network provenance correlatable without storing raw client IPs.
            sanitized["actor_ip_tag"] = stable_redaction_tag(actor_ip, label="ip")
        return redact_json(sanitized)
    return redact_json(safe_details)


def _get_audit_chain_key() -> bytes:
    global _AUDIT_CHAIN_KEY
    if _AUDIT_CHAIN_KEY is None:
        raw = os.environ.get("OPENCLAW_AUDIT_CHAIN_KEY") or os.environ.get(
            "MOLTBOT_AUDIT_CHAIN_KEY"
        )
        _AUDIT_CHAIN_KEY = raw.encode("utf-8") if raw else secrets.token_bytes(32)
    return _AUDIT_CHAIN_KEY


def _chain_hash(prev_hash: str, entry: Dict[str, Any]) -> str:
    payload = json.dumps(
        entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    # IMPORTANT: keep the append-only chain keyed, but avoid direct hashlib password
    # sinks here. CodeQL accepts the stdlib HMAC helper more reliably for audit data.
    return hmac.digest(
        _get_audit_chain_key(),
        f"{prev_hash}|{payload}".encode("utf-8"),
        "sha256",
    ).hex()


def _rotate_if_needed(path: str) -> None:
    max_bytes, backups = _audit_limits()
    if max_bytes <= 0 or backups < 0:
        return
    if not os.path.exists(path):
        return
    try:
        if os.path.getsize(path) < max_bytes:
            return
        if backups == 0:
            os.remove(path)
            return
        for idx in range(backups, 0, -1):
            src = f"{path}.{idx}"
            dst = f"{path}.{idx + 1}"
            if os.path.exists(src):
                if idx == backups:
                    os.remove(src)
                else:
                    os.replace(src, dst)
        os.replace(path, f"{path}.1")
    except Exception as exc:
        logger.error("Audit rotation failed: %s", exc)


def _read_last_entry_hash(path: str) -> str:
    # Best-effort bootstrap. Fall back to genesis if file is empty/unreadable.
    if not os.path.exists(path):
        return "GENESIS"
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size <= 0:
                return "GENESIS"
            step = min(size, 8192)
            f.seek(size - step)
            data = f.read().decode("utf-8", errors="ignore")
        lines = [line.strip() for line in data.splitlines() if line.strip()]
        if not lines:
            return "GENESIS"
        last = json.loads(lines[-1])
        last_hash = last.get("entry_hash")
        if isinstance(last_hash, str) and last_hash:
            return last_hash
    except Exception:
        pass
    return "GENESIS"


_LAST_HASH: Optional[str] = None
_AUDIT_WRITE_LOCK = threading.Lock()


def _write_audit_entry(entry: Dict[str, Any]) -> None:
    global _LAST_HASH
    try:
        os.makedirs(os.path.dirname(os.path.abspath(AUDIT_LOG_PATH)), exist_ok=True)
    except Exception:
        # Path may be relative to CWD with no parent folder.
        pass

    # CRITICAL: keep read->chain->append->state update atomic to avoid hash-chain forks.
    with _AUDIT_WRITE_LOCK:
        _rotate_if_needed(AUDIT_LOG_PATH)
        if _LAST_HASH is None:
            _LAST_HASH = _read_last_entry_hash(AUDIT_LOG_PATH)

        prev_hash = _LAST_HASH or "GENESIS"
        event_hash = _chain_hash(prev_hash, entry)
        wrapped = dict(entry)
        wrapped["prev_hash"] = prev_hash
        wrapped["entry_hash"] = event_hash

        line = json.dumps(wrapped, sort_keys=True, ensure_ascii=True) + "\n"
        try:
            with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
            _LAST_HASH = event_hash
        except Exception as exc:
            logger.error("Failed to write audit entry: %s", exc)


def _persistable_audit_entry(
    *,
    action: str,
    target: str,
    outcome: str,
    status_code: int,
    source: str,
    trace_id: str,
    details: Any,
) -> Dict[str, Any]:
    # IMPORTANT: persist only non-credential audit dimensions. Even boolean/token
    # presence derived fields keep residual CodeQL sensitive-storage alerts alive.
    return {
        "ts": time.time(),
        "source": str(source or "openclaw"),
        "trace_id": str(trace_id or uuid.uuid4().hex),
        "action": str(action or ""),
        "target": str(target or ""),
        "outcome": str(outcome or ""),
        "status_code": int(status_code),
        "details": redact_json(_json_safe(details)),
    }


def _emit_modern(
    *,
    action: str,
    target: str,
    outcome: str,
    token_info: Optional[Any] = None,
    status_code: int = 0,
    details: Optional[Dict[str, Any]] = None,
    request: Optional[Any] = None,
    source: str = "openclaw",
) -> Dict[str, Any]:
    details_dict = _sanitize_audit_details(details or {})
    trace_id = _resolve_trace_id(details_dict if isinstance(details_dict, dict) else {})
    entry = _persistable_audit_entry(
        action=action,
        target=target,
        outcome=outcome,
        status_code=int(status_code),
        source=source,
        trace_id=trace_id,
        details=details_dict,
    )
    _write_audit_entry(entry)
    logger.info(
        "AUDIT action=%s outcome=%s",
        action,
        outcome,
    )
    return entry


def _emit_legacy(
    event_type: str,
    actor_ip: str,
    ok: bool,
    provider: Optional[str] = None,
    error: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    details = {"actor_ip_tag": stable_redaction_tag(actor_ip, label="ip")}
    if provider:
        details["provider"] = provider
    if error:
        details["error"] = error
    if metadata:
        details["metadata"] = metadata
    return _emit_modern(
        action=event_type,
        target=provider or "settings",
        outcome="allow" if ok else "error",
        status_code=200 if ok else 500,
        details=details,
    )


def emit_audit_event(*args, **kwargs) -> Dict[str, Any]:
    """
    Backward-compatible audit API.

    Modern signature:
    - action, target, outcome, token_info=None, status_code=0, details=None, request=None

    Legacy signature:
    - event_type, actor_ip, ok, provider=None, error=None, metadata=None
    """
    if "action" in kwargs or "target" in kwargs or "outcome" in kwargs:
        return _emit_modern(
            action=kwargs.get("action", ""),
            target=kwargs.get("target", ""),
            outcome=kwargs.get("outcome", ""),
            token_info=kwargs.get("token_info"),
            status_code=kwargs.get("status_code", 0),
            details=kwargs.get("details"),
            request=kwargs.get("request"),
            source=kwargs.get("source", "openclaw"),
        )

    if len(args) >= 3 and isinstance(args[0], str) and isinstance(args[2], bool):
        event_type = args[0]
        actor_ip = str(args[1])
        ok = bool(args[2])
        provider = args[3] if len(args) > 3 else kwargs.get("provider")
        error = args[4] if len(args) > 4 else kwargs.get("error")
        metadata = args[5] if len(args) > 5 else kwargs.get("metadata")
        return _emit_legacy(event_type, actor_ip, ok, provider, error, metadata)

    raise TypeError("Unsupported emit_audit_event signature")


def audit_config_write(actor_ip: str, ok: bool, error: Optional[str] = None) -> None:
    # CRITICAL: keep convenience wrappers single-emit to avoid duplicate audit noise.
    emit_audit_event(
        action="config.update",
        target="config.json",
        outcome="allow" if ok else "error",
        status_code=200 if ok else 400,
        details=(
            {"actor_ip_tag": stable_redaction_tag(actor_ip, label="ip"), "error": error}
            if error
            else {"actor_ip_tag": stable_redaction_tag(actor_ip, label="ip")}
        ),
    )


def audit_secret_write(
    actor_ip: str, provider: str, ok: bool, error: Optional[str] = None
) -> None:
    emit_audit_event(
        action="secrets.write",
        target=provider,
        outcome="allow" if ok else "error",
        status_code=200 if ok else 500,
        details=(
            {
                "actor_ip_tag": stable_redaction_tag(actor_ip, label="ip"),
                "provider": provider,
                "error": error,
            }
            if error
            else {
                "actor_ip_tag": stable_redaction_tag(actor_ip, label="ip"),
                "provider": provider,
            }
        ),
    )


def audit_secret_delete(
    actor_ip: str, provider: str, ok: bool, error: Optional[str] = None
) -> None:
    emit_audit_event(
        action="secrets.delete",
        target=provider,
        outcome="allow" if ok else "error",
        status_code=200 if ok else 404,
        details=(
            {
                "actor_ip_tag": stable_redaction_tag(actor_ip, label="ip"),
                "provider": provider,
                "error": error,
            }
            if error
            else {
                "actor_ip_tag": stable_redaction_tag(actor_ip, label="ip"),
                "provider": provider,
            }
        ),
    )


def audit_llm_test(actor_ip: str, ok: bool, error: Optional[str] = None) -> None:
    emit_audit_event(
        action="llm.test_connection",
        target="llm",
        outcome="allow" if ok else "error",
        status_code=200 if ok else 500,
        details=(
            {"actor_ip_tag": stable_redaction_tag(actor_ip, label="ip"), "error": error}
            if error
            else {"actor_ip_tag": stable_redaction_tag(actor_ip, label="ip")}
        ),
    )
