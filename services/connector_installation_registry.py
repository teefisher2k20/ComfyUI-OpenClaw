from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .audit import emit_audit_event
    from .secret_store import SecretStore, get_secret_store
    from .state_dir import get_state_dir
except ImportError:
    from services.audit import emit_audit_event  # type: ignore
    from services.secret_store import SecretStore, get_secret_store  # type: ignore
    from services.state_dir import get_state_dir  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.services.connector_installation_registry")

INSTALLATION_STORE_FILE = "connector_installations.json"
MAX_INSTALLATION_AUDIT = 500


class InstallationStatus(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    ROTATING = "rotating"
    REVOKED = "revoked"
    DEACTIVATED = "deactivated"
    UNINSTALLED = "uninstalled"


_RESOLVABLE_STATUSES = frozenset(
    {InstallationStatus.ACTIVE.value, InstallationStatus.ROTATING.value}
)


@dataclass
class ConnectorInstallation:
    platform: str
    workspace_id: str
    installation_id: str
    token_refs: Dict[str, str] = field(default_factory=dict)
    status: str = InstallationStatus.CREATED.value
    updated_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    status_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "workspace_id": self.workspace_id,
            "installation_id": self.installation_id,
            "token_refs": dict(self.token_refs),
            "status": self.status,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "status_reason": self.status_reason,
            "metadata": dict(self.metadata),
        }


@dataclass
class InstallationAuditEvent:
    timestamp: float
    action: str
    installation_id: str
    platform: str
    workspace_id: str
    status: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "installation_id": self.installation_id,
            "platform": self.platform,
            "workspace_id": self.workspace_id,
            "status": self.status,
            "details": dict(self.details),
        }


@dataclass
class InstallationResolution:
    ok: bool
    installation: Optional[ConnectorInstallation] = None
    reject_reason: str = ""
    audit_code: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": self.ok,
            "reject_reason": self.reject_reason,
            "audit_code": self.audit_code,
        }
        if self.installation is not None:
            payload["installation"] = self.installation.to_public_dict()
        return payload


class ConnectorInstallationRegistry:
    """Persistent multi-workspace installation registry with fail-closed resolution."""

    def __init__(
        self,
        state_dir: Optional[str] = None,
        *,
        secret_store: Optional[SecretStore] = None,
    ):
        self._state_dir = Path(state_dir or get_state_dir())
        self._path = self._state_dir / INSTALLATION_STORE_FILE
        self._secret_store = secret_store or get_secret_store(str(self._state_dir))
        self._lock = threading.RLock()
        self._installations: Dict[str, ConnectorInstallation] = {}
        self._audit_trail: List[InstallationAuditEvent] = []
        self._load()

    def _normalize_identifier(self, value: str, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} must be non-empty")
        return text

    def _normalize_platform(self, platform: str) -> str:
        return self._normalize_identifier(platform, "platform").lower()

    def _store_token_refs(
        self, installation_id: str, token_values: Dict[str, str]
    ) -> Dict[str, str]:
        refs: Dict[str, str] = {}
        for key, value in (token_values or {}).items():
            name = self._normalize_identifier(key, "token_name")
            secret_value = self._normalize_identifier(value, f"token:{name}")
            ref = f"connector_installation:{installation_id}:{name}"
            self._secret_store.set_secret(ref, secret_value)
            refs[name] = ref
        return refs

    def _audit(
        self,
        action: str,
        installation: ConnectorInstallation,
        **details: Any,
    ) -> None:
        event = InstallationAuditEvent(
            timestamp=time.time(),
            action=action,
            installation_id=installation.installation_id,
            platform=installation.platform,
            workspace_id=installation.workspace_id,
            status=installation.status,
            details=details,
        )
        self._audit_trail.append(event)
        if len(self._audit_trail) > MAX_INSTALLATION_AUDIT:
            self._audit_trail = self._audit_trail[-MAX_INSTALLATION_AUDIT:]
        emit_audit_event(
            action=f"connector.installation.{action}",
            target=installation.installation_id,
            outcome="allow",
            status_code=200,
            details={
                "platform": installation.platform,
                "workspace_id": installation.workspace_id,
                "status": installation.status,
                **details,
            },
        )

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            installations = data.get("installations", [])
            audit_trail = data.get("audit_trail", [])
            self._installations = {}
            for item in installations:
                inst = ConnectorInstallation(
                    platform=item.get("platform", ""),
                    workspace_id=item.get("workspace_id", ""),
                    installation_id=item.get("installation_id", ""),
                    token_refs=dict(item.get("token_refs", {}) or {}),
                    status=item.get("status", InstallationStatus.CREATED.value),
                    updated_at=float(item.get("updated_at", time.time())),
                    created_at=float(item.get("created_at", time.time())),
                    status_reason=item.get("status_reason", ""),
                    metadata=dict(item.get("metadata", {}) or {}),
                )
                if inst.installation_id:
                    self._installations[inst.installation_id] = inst
            self._audit_trail = [
                InstallationAuditEvent(
                    timestamp=float(item.get("timestamp", time.time())),
                    action=item.get("action", "unknown"),
                    installation_id=item.get("installation_id", ""),
                    platform=item.get("platform", ""),
                    workspace_id=item.get("workspace_id", ""),
                    status=item.get("status", ""),
                    details=dict(item.get("details", {}) or {}),
                )
                for item in audit_trail
                if item.get("installation_id")
            ]
        except Exception as exc:
            logger.error("Failed to load connector installation registry: %s", exc)
            self._installations = {}
            self._audit_trail = []

    def _save(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "installations": [asdict(i) for i in self._installations.values()],
            "audit_trail": [event.to_dict() for event in self._audit_trail],
        }
        temp_path = self._path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp_path, self._path)

    def upsert_installation(
        self,
        *,
        platform: str,
        workspace_id: str,
        installation_id: str,
        token_values: Optional[Dict[str, str]] = None,
        token_refs: Optional[Dict[str, str]] = None,
        status: str = InstallationStatus.CREATED.value,
        metadata: Optional[Dict[str, Any]] = None,
        status_reason: str = "",
    ) -> ConnectorInstallation:
        with self._lock:
            normalized_platform = self._normalize_platform(platform)
            normalized_workspace = self._normalize_identifier(
                workspace_id, "workspace_id"
            )
            normalized_installation = self._normalize_identifier(
                installation_id, "installation_id"
            )
            now = time.time()
            refs = dict(token_refs or {})
            if token_values:
                refs.update(
                    self._store_token_refs(normalized_installation, token_values)
                )
            existing = self._installations.get(normalized_installation)
            if existing is None:
                created_at = now
            else:
                created_at = existing.created_at
                if not refs:
                    refs = dict(existing.token_refs)
            inst = ConnectorInstallation(
                platform=normalized_platform,
                workspace_id=normalized_workspace,
                installation_id=normalized_installation,
                token_refs=refs,
                status=self._normalize_identifier(status, "status"),
                updated_at=now,
                created_at=created_at,
                status_reason=status_reason,
                metadata=dict(metadata or (existing.metadata if existing else {})),
            )
            self._installations[normalized_installation] = inst
            self._audit("upsert", inst, token_ref_count=len(inst.token_refs))
            self._save()
            return inst

    def get_installation(self, installation_id: str) -> Optional[ConnectorInstallation]:
        with self._lock:
            inst = self._installations.get(str(installation_id).strip())
            return None if inst is None else ConnectorInstallation(**asdict(inst))

    def list_installations(
        self,
        *,
        platform: Optional[str] = None,
        workspace_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[ConnectorInstallation]:
        with self._lock:
            items = list(self._installations.values())
            if platform:
                items = [
                    i for i in items if i.platform == self._normalize_platform(platform)
                ]
            if workspace_id:
                items = [
                    i for i in items if i.workspace_id == str(workspace_id).strip()
                ]
            if status:
                items = [i for i in items if i.status == str(status).strip()]
            items.sort(
                key=lambda inst: (
                    inst.platform,
                    inst.workspace_id,
                    inst.installation_id,
                )
            )
            return [ConnectorInstallation(**asdict(inst)) for inst in items]

    def _transition(
        self,
        installation_id: str,
        target_status: str,
        *,
        reason: str = "",
        action: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> ConnectorInstallation:
        with self._lock:
            inst = self._installations.get(str(installation_id).strip())
            if inst is None:
                raise ValueError(f"Installation not found: {installation_id}")
            inst.status = target_status
            inst.status_reason = reason
            inst.updated_at = time.time()
            self._installations[inst.installation_id] = inst
            self._audit(action, inst, reason=reason, **(details or {}))
            self._save()
            return ConnectorInstallation(**asdict(inst))

    def activate_installation(
        self, installation_id: str, reason: str = ""
    ) -> ConnectorInstallation:
        return self._transition(
            installation_id,
            InstallationStatus.ACTIVE.value,
            reason=reason,
            action="activate",
        )

    def rotate_installation_tokens(
        self,
        installation_id: str,
        token_values: Dict[str, str],
        *,
        reason: str = "",
    ) -> ConnectorInstallation:
        with self._lock:
            inst = self._installations.get(str(installation_id).strip())
            if inst is None:
                raise ValueError(f"Installation not found: {installation_id}")
            refs = dict(inst.token_refs)
            refs.update(self._store_token_refs(inst.installation_id, token_values))
            inst.token_refs = refs
            inst.status = InstallationStatus.ROTATING.value
            inst.status_reason = reason
            inst.updated_at = time.time()
            self._installations[inst.installation_id] = inst
            self._audit(
                "rotate",
                inst,
                reason=reason,
                token_ref_count=len(inst.token_refs),
            )
            self._save()
            return ConnectorInstallation(**asdict(inst))

    def revoke_installation(
        self, installation_id: str, reason: str = ""
    ) -> ConnectorInstallation:
        return self._transition(
            installation_id,
            InstallationStatus.REVOKED.value,
            reason=reason,
            action="revoke",
        )

    def deactivate_installation(
        self, installation_id: str, reason: str = ""
    ) -> ConnectorInstallation:
        return self._transition(
            installation_id,
            InstallationStatus.DEACTIVATED.value,
            reason=reason,
            action="deactivate",
        )

    def uninstall_installation(
        self, installation_id: str, reason: str = ""
    ) -> ConnectorInstallation:
        with self._lock:
            inst = self._installations.get(str(installation_id).strip())
            if inst is None:
                raise ValueError(f"Installation not found: {installation_id}")
            for ref in list(inst.token_refs.values()):
                self._secret_store.clear_secret(ref)
            inst.status = InstallationStatus.UNINSTALLED.value
            inst.status_reason = reason
            inst.updated_at = time.time()
            self._installations[inst.installation_id] = inst
            self._audit("uninstall", inst, reason=reason)
            self._save()
            return ConnectorInstallation(**asdict(inst))

    def resolve_installation(
        self, platform: str, workspace_id: str
    ) -> InstallationResolution:
        with self._lock:
            normalized_platform = self._normalize_platform(platform)
            normalized_workspace = self._normalize_identifier(
                workspace_id, "workspace_id"
            )
            matches = [
                inst
                for inst in self._installations.values()
                if inst.platform == normalized_platform
                and inst.workspace_id == normalized_workspace
            ]
            eligible = [inst for inst in matches if inst.status in _RESOLVABLE_STATUSES]
            if len(eligible) > 1:
                return InstallationResolution(
                    ok=False,
                    reject_reason="ambiguous_binding",
                    audit_code="conn_install.resolve_ambiguous",
                )
            if not eligible:
                if not matches:
                    return InstallationResolution(
                        ok=False,
                        reject_reason="missing_binding",
                        audit_code="conn_install.resolve_missing",
                    )
                return InstallationResolution(
                    ok=False,
                    reject_reason="inactive_binding",
                    audit_code="conn_install.resolve_inactive",
                )
            inst = eligible[0]
            for token_name, ref in inst.token_refs.items():
                if not self._secret_store.get_secret(ref):
                    return InstallationResolution(
                        ok=False,
                        reject_reason=f"stale_token_ref:{token_name}",
                        audit_code="conn_install.resolve_stale_ref",
                    )
            return InstallationResolution(
                ok=True,
                installation=ConnectorInstallation(**asdict(inst)),
                audit_code="conn_install.resolve_ok",
            )

    def get_audit_trail(
        self,
        *,
        installation_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = self._audit_trail
            if installation_id:
                items = [
                    event
                    for event in items
                    if event.installation_id == str(installation_id).strip()
                ]
            return [event.to_dict() for event in items[-max(1, min(limit, 500)) :]]

    def diagnostics(self) -> Dict[str, Any]:
        with self._lock:
            counts: Dict[str, int] = {}
            for inst in self._installations.values():
                counts[inst.status] = counts.get(inst.status, 0) + 1
            return {
                "installation_count": len(self._installations),
                "status_counts": counts,
                "audit_events": len(self._audit_trail),
            }


_registry: Optional[ConnectorInstallationRegistry] = None


def get_connector_installation_registry(
    state_dir: Optional[str] = None,
) -> ConnectorInstallationRegistry:
    global _registry
    if _registry is None or state_dir is not None:
        _registry = ConnectorInstallationRegistry(state_dir=state_dir)
    return _registry
