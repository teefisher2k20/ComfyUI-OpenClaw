from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from connector.config import CommandClass
from connector.security_profile import ReplayGuard
from connector.transport_contract import CallbackContract, CallbackError, CallbackRecord

try:
    from .audit import emit_audit_event
    from .connector_installation_registry import (
        ConnectorInstallationRegistry,
        InstallationResolution,
        get_connector_installation_registry,
    )
except ImportError:
    from services.audit import emit_audit_event  # type: ignore
    from services.connector_installation_registry import (  # type: ignore
        ConnectorInstallationRegistry,
        InstallationResolution,
        get_connector_installation_registry,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.services.connector_callback_contract")

DEFAULT_CALLBACK_TIMESTAMP_DRIFT_SEC = 300
_CALLBACK_SIGNING_VERSION = "v1"


class CallbackDecisionCode(str, Enum):
    ACCEPT_PUBLIC = "cb_accept_public"
    ACCEPT_RUN = "cb_accept_run"
    ACCEPT_ADMIN = "cb_accept_admin"
    REQUIRE_APPROVAL = "cb_require_approval"
    REJECT_SIGNATURE = "cb_reject_signature"
    REJECT_TIMESTAMP = "cb_reject_timestamp"
    REJECT_PAYLOAD_HASH = "cb_reject_payload_hash"
    REJECT_REPLAY = "cb_reject_replay"
    REJECT_UNKNOWN_ACTION = "cb_reject_unknown_action"
    REJECT_MISSING_INSTALLATION = "cb_reject_missing_installation"
    REJECT_AMBIGUOUS_INSTALLATION = "cb_reject_ambiguous_installation"
    REJECT_INACTIVE_INSTALLATION = "cb_reject_inactive_installation"
    REJECT_STALE_TOKEN_REF = "cb_reject_stale_token_ref"
    REJECT_POLICY_DENIED = "cb_reject_policy_denied"
    REJECT_INVALID_ENVELOPE = "cb_reject_invalid_envelope"


@dataclass
class CallbackActorContext:
    is_admin: bool = False
    is_trusted: bool = False
    user_id: str = ""


@dataclass
class InteractiveCallbackEnvelope:
    signature: str
    timestamp: int
    request_id: str
    workspace_id: str
    action_type: str
    payload_hash: str

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "InteractiveCallbackEnvelope":
        try:
            timestamp = int(payload.get("timestamp", 0))
        except Exception as exc:
            raise ValueError("timestamp must be integer") from exc
        signature = str(payload.get("signature", "")).strip()
        request_id = str(payload.get("request_id", "")).strip()
        workspace_id = str(payload.get("workspace_id", "")).strip()
        action_type = str(payload.get("action_type", "")).strip()
        payload_hash = str(payload.get("payload_hash", "")).strip()
        if not all(
            [signature, request_id, workspace_id, action_type, payload_hash, timestamp]
        ):
            raise ValueError("callback envelope fields must be non-empty")
        return cls(
            signature=signature,
            timestamp=timestamp,
            request_id=request_id,
            workspace_id=workspace_id,
            action_type=action_type,
            payload_hash=payload_hash,
        )


@dataclass
class CallbackDecision:
    ok: bool
    decision_code: str
    command_class: str = ""
    requires_approval: bool = False
    callback_id: str = ""
    installation_id: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "decision_code": self.decision_code,
            "command_class": self.command_class,
            "requires_approval": self.requires_approval,
            "callback_id": self.callback_id,
            "installation_id": self.installation_id,
            "message": self.message,
        }


class ConnectorCallbackContract:
    """Platform-agnostic interactive callback validation and policy gate."""

    def __init__(
        self,
        signing_secret: str,
        *,
        installation_registry: Optional[ConnectorInstallationRegistry] = None,
        replay_guard: Optional[ReplayGuard] = None,
        callback_contract: Optional[CallbackContract] = None,
        action_policy_map: Optional[Dict[str, str]] = None,
        timestamp_drift_sec: int = DEFAULT_CALLBACK_TIMESTAMP_DRIFT_SEC,
    ):
        self._signing_secret = str(signing_secret or "")
        self._installation_registry = (
            installation_registry or get_connector_installation_registry()
        )
        self._replay_guard = replay_guard or ReplayGuard(window_sec=300, max_entries=5000)
        self._callback_contract = callback_contract or CallbackContract()
        self._action_policy_map = dict(action_policy_map or {})
        self._timestamp_drift_sec = max(1, int(timestamp_drift_sec))

    @staticmethod
    def compute_payload_hash(payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def compute_signature(self, envelope: InteractiveCallbackEnvelope) -> str:
        base = (
            f"{_CALLBACK_SIGNING_VERSION}:{envelope.timestamp}:{envelope.request_id}:"
            f"{envelope.workspace_id}:{envelope.action_type}:{envelope.payload_hash}"
        )
        return hmac.new(
            self._signing_secret.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def build_envelope(
        self,
        *,
        request_id: str,
        workspace_id: str,
        action_type: str,
        payload: Dict[str, Any],
        timestamp: Optional[int] = None,
    ) -> InteractiveCallbackEnvelope:
        ts = int(timestamp or time.time())
        payload_hash = self.compute_payload_hash(payload)
        unsigned = InteractiveCallbackEnvelope(
            signature="",
            timestamp=ts,
            request_id=request_id,
            workspace_id=workspace_id,
            action_type=action_type,
            payload_hash=payload_hash,
        )
        unsigned.signature = self.compute_signature(unsigned)
        return unsigned

    def _map_installation_reject(
        self, resolution: InstallationResolution
    ) -> CallbackDecision:
        reason = resolution.reject_reason
        if reason == "missing_binding":
            code = CallbackDecisionCode.REJECT_MISSING_INSTALLATION.value
        elif reason == "ambiguous_binding":
            code = CallbackDecisionCode.REJECT_AMBIGUOUS_INSTALLATION.value
        elif reason == "inactive_binding":
            code = CallbackDecisionCode.REJECT_INACTIVE_INSTALLATION.value
        elif reason.startswith("stale_token_ref"):
            code = CallbackDecisionCode.REJECT_STALE_TOKEN_REF.value
        else:
            code = CallbackDecisionCode.REJECT_INVALID_ENVELOPE.value
        return CallbackDecision(ok=False, decision_code=code, message=reason)

    def _resolve_action_policy(self, action_type: str) -> Optional[str]:
        if action_type in self._action_policy_map:
            return self._action_policy_map[action_type]
        for key, value in self._action_policy_map.items():
            if key.endswith("*") and action_type.startswith(key[:-1]):
                return value
        return None

    def _audit_decision(
        self,
        *,
        platform: str,
        envelope: Optional[InteractiveCallbackEnvelope],
        decision: CallbackDecision,
    ) -> None:
        details = {
            "platform": platform,
            "decision_code": decision.decision_code,
            "command_class": decision.command_class,
            "requires_approval": decision.requires_approval,
            "callback_id": decision.callback_id,
            "installation_id": decision.installation_id,
        }
        if envelope is not None:
            details.update(
                {
                    "request_id": envelope.request_id,
                    "workspace_id": envelope.workspace_id,
                    "action_type": envelope.action_type,
                }
            )
        emit_audit_event(
            action="connector.callback.evaluate",
            target=decision.installation_id or "connector_callback",
            outcome="allow" if decision.ok else "deny",
            status_code=200 if decision.ok else 403,
            details=details,
        )

    def evaluate(
        self,
        *,
        platform: str,
        envelope_dict: Dict[str, Any],
        payload: Dict[str, Any],
        actor: CallbackActorContext,
    ) -> CallbackDecision:
        try:
            envelope = InteractiveCallbackEnvelope.from_dict(envelope_dict)
        except ValueError as exc:
            decision = CallbackDecision(
                ok=False,
                decision_code=CallbackDecisionCode.REJECT_INVALID_ENVELOPE.value,
                message=str(exc),
            )
            self._audit_decision(platform=platform, envelope=None, decision=decision)
            return decision

        now = int(time.time())
        if abs(now - envelope.timestamp) > self._timestamp_drift_sec:
            decision = CallbackDecision(
                ok=False,
                decision_code=CallbackDecisionCode.REJECT_TIMESTAMP.value,
                message="timestamp_out_of_window",
            )
            self._audit_decision(platform=platform, envelope=envelope, decision=decision)
            return decision

        expected_hash = self.compute_payload_hash(payload)
        if not hmac.compare_digest(expected_hash, envelope.payload_hash):
            decision = CallbackDecision(
                ok=False,
                decision_code=CallbackDecisionCode.REJECT_PAYLOAD_HASH.value,
                message="payload_hash_mismatch",
            )
            self._audit_decision(platform=platform, envelope=envelope, decision=decision)
            return decision

        # CRITICAL: interactive callback signatures must remain canonical and constant-time.
        expected_signature = self.compute_signature(envelope)
        if not self._signing_secret or not hmac.compare_digest(
            expected_signature, envelope.signature
        ):
            decision = CallbackDecision(
                ok=False,
                decision_code=CallbackDecisionCode.REJECT_SIGNATURE.value,
                message="signature_mismatch",
            )
            self._audit_decision(platform=platform, envelope=envelope, decision=decision)
            return decision

        if self._replay_guard.is_duplicate(envelope.request_id):
            decision = CallbackDecision(
                ok=False,
                decision_code=CallbackDecisionCode.REJECT_REPLAY.value,
                message="request_id_replay",
            )
            self._audit_decision(platform=platform, envelope=envelope, decision=decision)
            return decision

        resolution = self._installation_registry.resolve_installation(
            platform, envelope.workspace_id
        )
        if not resolution.ok or resolution.installation is None:
            decision = self._map_installation_reject(resolution)
            self._audit_decision(platform=platform, envelope=envelope, decision=decision)
            return decision

        command_class = self._resolve_action_policy(envelope.action_type)
        if command_class is None:
            decision = CallbackDecision(
                ok=False,
                decision_code=CallbackDecisionCode.REJECT_UNKNOWN_ACTION.value,
                installation_id=resolution.installation.installation_id,
                message="unknown_action_type",
            )
            self._audit_decision(platform=platform, envelope=envelope, decision=decision)
            return decision

        record = self._callback_contract.create(
            idempotency_key=envelope.request_id,
            payload=payload,
            metadata={
                "platform": platform,
                "workspace_id": envelope.workspace_id,
                "action_type": envelope.action_type,
                "installation_id": resolution.installation.installation_id,
            },
        )

        if command_class == CommandClass.PUBLIC.value:
            decision = CallbackDecision(
                ok=True,
                decision_code=CallbackDecisionCode.ACCEPT_PUBLIC.value,
                command_class=command_class,
                callback_id=record.callback_id,
                installation_id=resolution.installation.installation_id,
            )
        elif command_class == CommandClass.RUN.value:
            if actor.is_admin or actor.is_trusted:
                decision = CallbackDecision(
                    ok=True,
                    decision_code=CallbackDecisionCode.ACCEPT_RUN.value,
                    command_class=command_class,
                    callback_id=record.callback_id,
                    installation_id=resolution.installation.installation_id,
                )
            else:
                decision = CallbackDecision(
                    ok=False,
                    decision_code=CallbackDecisionCode.REQUIRE_APPROVAL.value,
                    command_class=command_class,
                    requires_approval=True,
                    callback_id=record.callback_id,
                    installation_id=resolution.installation.installation_id,
                )
        elif command_class == CommandClass.ADMIN.value:
            if actor.is_admin:
                decision = CallbackDecision(
                    ok=True,
                    decision_code=CallbackDecisionCode.ACCEPT_ADMIN.value,
                    command_class=command_class,
                    callback_id=record.callback_id,
                    installation_id=resolution.installation.installation_id,
                )
            else:
                decision = CallbackDecision(
                    ok=False,
                    decision_code=CallbackDecisionCode.REJECT_POLICY_DENIED.value,
                    command_class=command_class,
                    callback_id=record.callback_id,
                    installation_id=resolution.installation.installation_id,
                    message="admin_required",
                )
        else:
            decision = CallbackDecision(
                ok=False,
                decision_code=CallbackDecisionCode.REJECT_UNKNOWN_ACTION.value,
                installation_id=resolution.installation.installation_id,
                message="unsupported_command_class",
            )
        self._audit_decision(platform=platform, envelope=envelope, decision=decision)
        return decision

    def get_record(self, request_id: str) -> Optional[CallbackRecord]:
        return self._callback_contract.get_by_idempotency_key(str(request_id).strip())

    def acknowledge_request(self, request_id: str) -> CallbackRecord:
        record = self.get_record(request_id)
        if record is None:
            raise CallbackError(f"Callback not found for request_id={request_id}")
        return self._callback_contract.acknowledge(record.callback_id)

    def complete_request(self, request_id: str) -> CallbackRecord:
        record = self.get_record(request_id)
        if record is None:
            raise CallbackError(f"Callback not found for request_id={request_id}")
        return self._callback_contract.deliver(record.callback_id)
