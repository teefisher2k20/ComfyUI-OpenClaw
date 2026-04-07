"""
S58 — Bridge Token Lifecycle v2.

Manages bridge token issuance, rotation with overlap windows, revocation,
and expiry enforcement. All mutations emit structured audit events.

Security properties:
- Tokens have bounded lifetimes (expires_at)
- Rotation provides a controlled overlap window for seamless handover
- Revocation takes immediate effect
- Lifecycle decisions are deterministic and auditable
"""

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .sidecar.bridge_contract import BridgeScope, DeviceToken, TokenStatus
except ImportError:
    from services.sidecar.bridge_contract import (  # type: ignore
        BridgeScope,
        DeviceToken,
        TokenStatus,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.services.bridge_token_lifecycle")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TTL_SEC = 3600  # 1 hour
MAX_TTL_SEC = 86400  # 24 hours
DEFAULT_OVERLAP_SEC = 300  # 5 minutes
MAX_OVERLAP_SEC = 1800  # 30 minutes
TOKEN_BYTE_LENGTH = 32  # 256-bit random tokens


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


@dataclass
class TokenValidationResult:
    """Result of token validation."""

    ok: bool
    token: Optional[DeviceToken] = None
    reject_reason: str = ""
    is_overlap: bool = False  # True if token accepted within overlap window


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


@dataclass
class TokenAuditEvent:
    """Structured audit event for token lifecycle operations."""

    timestamp: float
    action: str  # issue, rotate, revoke, expire, validate_reject
    token_id: str
    device_id: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "token_id": self.token_id,
            "device_id": self.device_id,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Bridge Token Store
# ---------------------------------------------------------------------------


class BridgeTokenStore:
    """
    In-memory + persisted bridge token registry.

    All tokens are stored by token_id and resolved by bounded constant-time scan.
    Persistence is optional (state_dir may be None for test usage).
    """

    MAX_TOKENS_PER_DEVICE = 5  # Cap active tokens per device
    MAX_AUDIT_TRAIL = 200  # Cap total audit entries

    def __init__(self, state_dir: Optional[str] = None):
        self._tokens: Dict[str, DeviceToken] = {}  # token_id → DeviceToken
        self._audit_trail: List[TokenAuditEvent] = []
        self._state_dir = state_dir
        self._store_path: Optional[Path] = None
        if state_dir:
            self._store_path = Path(state_dir) / "bridge_tokens.json"
            self._load()

    # --- Persistence ---

    def _load(self) -> None:
        """Load persisted tokens from disk."""
        if not self._store_path or not self._store_path.exists():
            return
        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for td in data.get("tokens", []):
                token = DeviceToken(
                    device_id=td["device_id"],
                    device_token=td["device_token"],
                    scopes=[BridgeScope(s) for s in td.get("scopes", [])],
                    expires_at=td.get("expires_at"),
                    token_id=td.get("token_id", ""),
                    issued_at=td.get("issued_at", 0.0),
                    status=td.get("status", TokenStatus.ACTIVE.value),
                    replaces=td.get("replaces", ""),
                    overlap_until=td.get("overlap_until"),
                )
                self._tokens[token.token_id] = token
            logger.info(f"S58: Loaded {len(self._tokens)} bridge tokens")
        except Exception as e:
            logger.error(f"S58: Failed to load bridge tokens: {e}")

    def _save(self) -> None:
        """Persist tokens to disk."""
        if not self._store_path:
            return
        try:
            os.makedirs(self._store_path.parent, exist_ok=True)
            tokens_data = []
            for t in self._tokens.values():
                tokens_data.append(
                    {
                        "device_id": t.device_id,
                        "device_token": t.device_token,
                        "scopes": [
                            s.value if isinstance(s, BridgeScope) else s
                            for s in t.scopes
                        ],
                        "expires_at": t.expires_at,
                        "token_id": t.token_id,
                        "issued_at": t.issued_at,
                        "status": t.status,
                        "replaces": t.replaces,
                        "overlap_until": t.overlap_until,
                    }
                )
            temp = self._store_path.with_suffix(".tmp")
            with open(temp, "w", encoding="utf-8") as f:
                json.dump({"tokens": tokens_data}, f, indent=2)
            temp.replace(self._store_path)
        except Exception as e:
            logger.error(f"S58: Failed to persist bridge tokens: {e}")

    # --- Token lookup ---

    def _resolve_token_for_value(
        self, token_value: str
    ) -> Tuple[Optional[str], Optional[DeviceToken]]:
        # IMPORTANT: keep lookup on bounded constant-time comparison instead of
        # a derived hash index; hashing the presented token is the residual sink.
        for token_id, token in self._tokens.items():
            if secrets.compare_digest(token.device_token, token_value):
                return token_id, token
        return None, None

    # --- Audit ---

    def _emit_audit(
        self, action: str, token_id: str, device_id: str, **details: Any
    ) -> None:
        """Record a structured audit event."""
        event = TokenAuditEvent(
            timestamp=time.time(),
            action=action,
            token_id=token_id,
            device_id=device_id,
            details=details,
        )
        self._audit_trail.append(event)
        # Cap trail size
        if len(self._audit_trail) > self.MAX_AUDIT_TRAIL:
            self._audit_trail = self._audit_trail[-self.MAX_AUDIT_TRAIL :]
        logger.info(f"S58: Audit [{action}] token={token_id} device={device_id}")

    def get_audit_trail(self, *, device_id: Optional[str] = None) -> List[dict]:
        """Return audit trail, optionally filtered by device_id."""
        trail = self._audit_trail
        if device_id:
            trail = [e for e in trail if e.device_id == device_id]
        return [e.to_dict() for e in trail]

    # --- Issue ---

    def issue_token(
        self,
        device_id: str,
        scopes: Optional[List[BridgeScope]] = None,
        ttl_sec: int = DEFAULT_TTL_SEC,
    ) -> DeviceToken:
        """
        Issue a new bridge token for a device.

        Args:
            device_id: Target device identifier
            scopes: Authorized scopes (defaults to read-only)
            ttl_sec: Token lifetime in seconds (capped at MAX_TTL_SEC)
        """
        ttl_sec = min(max(ttl_sec, 60), MAX_TTL_SEC)  # Bounded 60s..24h
        now = time.time()

        token_id = f"bt_{secrets.token_hex(8)}"
        token_value = secrets.token_urlsafe(TOKEN_BYTE_LENGTH)

        token = DeviceToken(
            device_id=device_id,
            device_token=token_value,
            scopes=scopes or [BridgeScope.JOB_STATUS, BridgeScope.CONFIG_READ],
            expires_at=now + ttl_sec,
            token_id=token_id,
            issued_at=now,
            status=TokenStatus.ACTIVE.value,
        )

        self._tokens[token_id] = token
        self._emit_audit("issue", token_id, device_id, ttl_sec=ttl_sec)
        self._save()

        return token

    # --- Rotate ---

    def rotate_token(
        self,
        old_token_id: str,
        overlap_sec: int = DEFAULT_OVERLAP_SEC,
        ttl_sec: int = DEFAULT_TTL_SEC,
        scopes: Optional[List[BridgeScope]] = None,
    ) -> Tuple[DeviceToken, DeviceToken]:
        """
        Rotate a bridge token, returning (new_token, old_token_updated).

        The old token stays valid until min(old.expires_at, now + overlap_sec).
        After overlap_until, the old token is deterministically rejected.
        """
        old_token = self._tokens.get(old_token_id)
        if not old_token:
            raise ValueError(f"Token {old_token_id} not found")
        if old_token.status != TokenStatus.ACTIVE.value:
            raise ValueError(
                f"Cannot rotate non-active token (status={old_token.status})"
            )

        overlap_sec = min(max(overlap_sec, 30), MAX_OVERLAP_SEC)
        now = time.time()

        # Set overlap window on old token
        old_expires = old_token.expires_at or (now + 86400)
        old_token.overlap_until = min(now + overlap_sec, old_expires)

        # Issue new token linked to old
        new_token = self.issue_token(
            device_id=old_token.device_id,
            scopes=scopes or old_token.scopes,
            ttl_sec=ttl_sec,
        )
        new_token.replaces = old_token_id

        self._tokens[old_token_id] = old_token
        self._tokens[new_token.token_id] = new_token
        self._emit_audit(
            "rotate",
            new_token.token_id,
            old_token.device_id,
            old_token_id=old_token_id,
            overlap_sec=overlap_sec,
        )
        self._save()

        return new_token, old_token

    # --- Revoke ---

    def revoke_token(self, token_id: str, reason: str = "") -> DeviceToken:
        """
        Immediately revoke a token. Takes effect on next validation.
        """
        token = self._tokens.get(token_id)
        if not token:
            raise ValueError(f"Token {token_id} not found")

        token.status = TokenStatus.REVOKED.value
        self._tokens[token_id] = token
        self._emit_audit(
            "revoke",
            token_id,
            token.device_id,
            reason=reason,
        )
        self._save()

        return token

    # --- Validate ---

    def validate_token(
        self, token_value: str, required_scope: Optional[str] = None
    ) -> TokenValidationResult:
        """
        Validate a token value against the store.

        Checks (in order):
        1. Token exists
        2. Token not revoked
        3. Token not expired (respects overlap_until window)
        4. Required scope (if specified)

        Returns TokenValidationResult with ok, reject_reason, and token metadata.
        """
        token_id, token = self._resolve_token_for_value(token_value)
        if token_id is None or token is None:
            return TokenValidationResult(ok=False, reject_reason="unknown_token")

        now = time.time()

        # Check revocation (immediate, non-negotiable)
        if token.status == TokenStatus.REVOKED.value:
            self._emit_audit(
                "validate_reject", token_id, token.device_id, reason="revoked"
            )
            return TokenValidationResult(
                ok=False, token=token, reject_reason="token_revoked"
            )

        # Check expiry
        if token.expires_at and now > token.expires_at:
            token.status = TokenStatus.EXPIRED.value
            self._tokens[token_id] = token
            self._emit_audit("expire", token_id, token.device_id)
            return TokenValidationResult(
                ok=False, token=token, reject_reason="token_expired"
            )

        # Check overlap window (old token in rotation — still valid but soon to expire)
        is_overlap = False
        if token.overlap_until:
            if now > token.overlap_until:
                # Overlap window passed — reject deterministically
                token.status = TokenStatus.EXPIRED.value
                self._tokens[token_id] = token
                self._emit_audit(
                    "validate_reject",
                    token_id,
                    token.device_id,
                    reason="overlap_window_expired",
                )
                return TokenValidationResult(
                    ok=False, token=token, reject_reason="overlap_window_expired"
                )
            is_overlap = True

        # Check scope
        if required_scope:
            token_scopes = {
                s.value if isinstance(s, BridgeScope) else s for s in token.scopes
            }
            if required_scope not in token_scopes:
                self._emit_audit(
                    "validate_reject",
                    token_id,
                    token.device_id,
                    reason="insufficient_scope",
                    required=required_scope,
                )
                return TokenValidationResult(
                    ok=False, token=token, reject_reason="insufficient_scope"
                )

        return TokenValidationResult(ok=True, token=token, is_overlap=is_overlap)

    # --- Listing ---

    def list_tokens(
        self, *, device_id: Optional[str] = None, active_only: bool = False
    ) -> List[DeviceToken]:
        """List tokens, optionally filtered."""
        tokens = list(self._tokens.values())
        if device_id:
            tokens = [t for t in tokens if t.device_id == device_id]
        if active_only:
            tokens = [t for t in tokens if t.status == TokenStatus.ACTIVE.value]
        return tokens

    # --- Cleanup ---

    def cleanup_expired(self) -> int:
        """Remove expired/revoked tokens. Returns count removed."""
        now = time.time()
        to_remove = []
        for tid, token in self._tokens.items():
            if token.status == TokenStatus.REVOKED.value:
                to_remove.append(tid)
            elif token.expires_at and now > token.expires_at:
                to_remove.append(tid)
            elif token.overlap_until and now > token.overlap_until:
                to_remove.append(tid)
        for tid in to_remove:
            self._tokens.pop(tid)
        if to_remove:
            self._save()
            logger.info(f"S58: Cleaned up {len(to_remove)} expired/revoked tokens")
        return len(to_remove)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[BridgeTokenStore] = None


def get_token_store(state_dir: Optional[str] = None) -> BridgeTokenStore:
    """Get or create the global bridge token store."""
    global _store
    if _store is None:
        if state_dir is None:
            try:
                from .state_dir import get_state_dir

                state_dir = get_state_dir()
            except ImportError:
                try:
                    from services.state_dir import get_state_dir  # type: ignore

                    state_dir = get_state_dir()
                except ImportError:
                    state_dir = None
        _store = BridgeTokenStore(state_dir)
    return _store
