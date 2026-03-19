from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from connector.config import ConnectorConfig

try:
    from services.audit import emit_audit_event
    from services.connector_installation_registry import (
        ConnectorInstallation,
        ConnectorInstallationRegistry,
        InstallationResolution,
        InstallationStatus,
        get_connector_installation_registry,
    )
    from services.secret_store import SecretStore, get_secret_store
    from services.state_dir import get_state_dir
except ImportError:  # pragma: no cover
    from services.audit import emit_audit_event  # type: ignore
    from services.connector_installation_registry import (  # type: ignore
        ConnectorInstallation,
        ConnectorInstallationRegistry,
        InstallationResolution,
        InstallationStatus,
        get_connector_installation_registry,
    )
    from services.secret_store import SecretStore, get_secret_store  # type: ignore
    from services.state_dir import get_state_dir  # type: ignore

logger = logging.getLogger(__name__)

SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
SLACK_OAUTH_STATE_FILE = "slack_oauth_states.json"

_INVALID_TOKEN_ERRORS = frozenset(
    {"account_inactive", "invalid_auth", "not_authed", "token_revoked"}
)
_DEGRADED_TOKEN_ERRORS = frozenset({"ratelimited", "request_timeout", "fatal_error"})


def _load_aiohttp():
    try:
        import aiohttp  # type: ignore
    except ModuleNotFoundError:
        return None
    return aiohttp


class SlackInstallationManager:
    def __init__(
        self,
        config: ConnectorConfig,
        *,
        registry: Optional[ConnectorInstallationRegistry] = None,
        secret_store: Optional[SecretStore] = None,
        state_dir: Optional[str] = None,
    ):
        self.config = config
        self._state_dir = Path(state_dir or get_state_dir())
        self._state_path = self._state_dir / SLACK_OAUTH_STATE_FILE
        self._registry = registry or get_connector_installation_registry(
            state_dir=str(self._state_dir)
        )
        self._secret_store = secret_store or get_secret_store(str(self._state_dir))
        self._lock = threading.RLock()
        self._states: Dict[str, Dict[str, Any]] = {}
        self._load_states()

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.config.slack_client_id and self.config.slack_client_secret)

    def resolve_redirect_uri(self) -> str:
        if self.config.slack_oauth_redirect_uri:
            return str(self.config.slack_oauth_redirect_uri).strip()
        if self.config.public_base_url:
            base = self.config.public_base_url.rstrip("/")
            path = self.config.slack_oauth_callback_path or "/slack/oauth/callback"
            return f"{base}{path}"
        return ""

    def can_handle_oauth(self) -> bool:
        return self.oauth_enabled and bool(self.resolve_redirect_uri())

    def _save_states(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self._state_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self._states, indent=2), encoding="utf-8")
        os.replace(temp_path, self._state_path)

    def _load_states(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._states = data
        except Exception as exc:
            logger.warning("Failed to load Slack OAuth state store: %s", exc)
            self._states = {}
        self._prune_expired_states()

    def _prune_expired_states(self) -> None:
        now = time.time()
        ttl = max(60, int(self.config.slack_oauth_state_ttl_sec or 600))
        changed = False
        for key, payload in list(self._states.items()):
            created_at = float(payload.get("created_at", 0) or 0)
            if not created_at or (now - created_at) > ttl:
                self._states.pop(key, None)
                changed = True
        if changed:
            self._save_states()

    def issue_install_state(self) -> str:
        if not self.can_handle_oauth():
            raise RuntimeError("Slack OAuth flow not configured")
        with self._lock:
            self._prune_expired_states()
            state = secrets.token_urlsafe(32)
            self._states[state] = {"created_at": time.time()}
            self._save_states()
            return state

    def consume_install_state(self, state: str) -> bool:
        with self._lock:
            self._prune_expired_states()
            payload = self._states.pop(str(state or "").strip(), None)
            if payload is None:
                return False
            self._save_states()
            return True

    def build_install_url(self, state: str) -> str:
        params = {
            "client_id": self.config.slack_client_id or "",
            "scope": ",".join(self.config.slack_oauth_scopes or []),
            "redirect_uri": self.resolve_redirect_uri(),
            "state": state,
        }
        return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        aiohttp = _load_aiohttp()
        if aiohttp is None:
            raise RuntimeError("aiohttp required for Slack OAuth exchange")
        payload = {
            "client_id": self.config.slack_client_id or "",
            "client_secret": self.config.slack_client_secret or "",
            "code": str(code or "").strip(),
            "redirect_uri": self.resolve_redirect_uri(),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SLACK_OAUTH_ACCESS_URL,
                data=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200 or not data.get("ok"):
                    raise RuntimeError(
                        f"slack_oauth_exchange_failed:{resp.status}:{data.get('error', 'unknown')}"
                    )
                return data

    def _normalize_workspace_id(self, payload: Dict[str, Any]) -> str:
        workspace_id = (
            (payload.get("team") or {}).get("id")
            or payload.get("team_id")
            or (
                (payload.get("enterprise") or {}).get("id")
                if payload.get("enterprise")
                else ""
            )
        )
        workspace_id = str(workspace_id or "").strip()
        if not workspace_id:
            raise ValueError("workspace_id_missing")
        return workspace_id

    def installation_id_for_workspace(self, workspace_id: str) -> str:
        return f"slack:{str(workspace_id or '').strip()}"

    def metadata_from_oauth_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        team = dict(payload.get("team", {}) or {})
        enterprise = dict(payload.get("enterprise", {}) or {})
        authed_user = dict(payload.get("authed_user", {}) or {})
        metadata = {
            "workspace_name": str(team.get("name", "") or "").strip(),
            "enterprise_id": str(enterprise.get("id", "") or "").strip(),
            "enterprise_name": str(enterprise.get("name", "") or "").strip(),
            "bot_user_id": str(payload.get("bot_user_id", "") or "").strip(),
            "app_id": str(payload.get("app_id", "") or "").strip(),
            "scope": str(payload.get("scope", "") or "").strip(),
            "authed_user_id": str(authed_user.get("id", "") or "").strip(),
            "token_type": str(payload.get("token_type", "") or "").strip(),
            "transport_mode": self.config.slack_mode,
        }
        return {key: value for key, value in metadata.items() if value}

    def upsert_from_oauth_payload(
        self, payload: Dict[str, Any]
    ) -> ConnectorInstallation:
        workspace_id = self._normalize_workspace_id(payload)
        installation_id = self.installation_id_for_workspace(workspace_id)
        token_values = {"bot_token": str(payload.get("access_token", "") or "").strip()}
        if self.config.slack_app_token:
            token_values["app_token"] = self.config.slack_app_token
        if not token_values["bot_token"]:
            raise ValueError("bot_token_missing")

        metadata = self.metadata_from_oauth_payload(payload)
        existing = self._registry.get_installation(installation_id)
        if existing is not None:
            rotated = self._registry.rotate_installation_tokens(
                installation_id,
                token_values,
                reason="slack_oauth_reinstall",
            )
            inst = self._registry.upsert_installation(
                platform="slack",
                workspace_id=workspace_id,
                installation_id=installation_id,
                token_refs=rotated.token_refs,
                status=rotated.status,
                metadata=metadata,
                status_reason="slack_oauth_reinstall",
            )
        else:
            inst = self._registry.upsert_installation(
                platform="slack",
                workspace_id=workspace_id,
                installation_id=installation_id,
                token_values=token_values,
                status=InstallationStatus.CREATED.value,
                metadata=metadata,
                status_reason="slack_oauth_install",
            )
        inst = self._registry.activate_installation(
            installation_id, reason="slack_oauth_complete"
        )
        inst = self._registry.update_installation_health(
            installation_id,
            health_code="ok",
            reason="slack_oauth_complete",
            details={"workspace_id": workspace_id},
        )
        emit_audit_event(
            action="connector.slack.oauth.install",
            target=installation_id,
            outcome="allow",
            status_code=200,
            details={
                "workspace_id": workspace_id,
                "workspace_name": metadata.get("workspace_name", ""),
                "transport_mode": self.config.slack_mode,
            },
        )
        return inst

    def extract_workspace_id(self, payload: Dict[str, Any]) -> str:
        if isinstance(payload.get("team_id"), str) and payload.get("team_id"):
            return str(payload["team_id"]).strip()
        team = payload.get("team") or {}
        if isinstance(team, dict) and team.get("id"):
            return str(team.get("id")).strip()
        authorizations = payload.get("authorizations") or []
        if isinstance(authorizations, list) and authorizations:
            workspace_id = str((authorizations[0] or {}).get("team_id", "")).strip()
            if workspace_id:
                return workspace_id
        event = payload.get("event") or {}
        workspace_id = str(event.get("team", "") or "").strip()
        return workspace_id

    def resolve_workspace_tokens(
        self, workspace_id: str
    ) -> Tuple[InstallationResolution, Dict[str, str]]:
        resolution = self._registry.resolve_installation("slack", workspace_id)
        if not resolution.ok or resolution.installation is None:
            emit_audit_event(
                action="connector.slack.resolve",
                target=workspace_id or "unknown_workspace",
                outcome="deny",
                status_code=409,
                details={
                    "workspace_id": workspace_id,
                    "reject_reason": resolution.reject_reason,
                    "health_code": resolution.health_code,
                },
            )
            return resolution, {}

        tokens: Dict[str, str] = {}
        for token_name, ref in resolution.installation.token_refs.items():
            secret = self._secret_store.get_secret(
                ref, tenant_id=resolution.installation.tenant_id
            )
            if secret:
                tokens[token_name] = secret
        return resolution, tokens

    def bot_user_id_for_installation(
        self, installation: Optional[ConnectorInstallation]
    ) -> str:
        if installation is None:
            return ""
        return str(
            (
                installation.metadata.get("bot_user_id", "")
                if installation.metadata
                else ""
            )
            or ""
        ).strip()

    def mark_installation_health(
        self,
        installation_id: str,
        *,
        health_code: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._registry.update_installation_health(
            installation_id,
            health_code=health_code,
            reason=reason,
            details=details,
        )

    def uninstall_installation(self, installation_id: str, *, reason: str) -> None:
        self._registry.uninstall_installation(installation_id, reason=reason)

    def mark_resolution_success(self, installation_id: str, workspace_id: str) -> None:
        self._registry.update_installation_health(
            installation_id,
            health_code="ok",
            reason="workspace_resolved",
            details={"workspace_id": workspace_id},
        )

    def classify_error_health(self, error_code: str, status_code: int = 0) -> str:
        normalized = str(error_code or "").strip().lower()
        if normalized in _INVALID_TOKEN_ERRORS or status_code in (401, 403):
            return "invalid_token"
        if (
            normalized in _DEGRADED_TOKEN_ERRORS
            or status_code == 429
            or status_code >= 500
        ):
            return "degraded"
        return "degraded"

    def mark_api_error(
        self,
        installation_id: str,
        *,
        error_code: str,
        status_code: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        health_code = self.classify_error_health(error_code, status_code=status_code)
        self.mark_installation_health(
            installation_id,
            health_code=health_code,
            reason=error_code or f"http_{status_code}",
            details=details,
        )
        return health_code
