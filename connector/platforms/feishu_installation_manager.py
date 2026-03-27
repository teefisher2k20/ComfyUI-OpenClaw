from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from connector.config import ConnectorConfig

try:
    from services.connector_installation_registry import (
        ConnectorInstallation,
        ConnectorInstallationRegistry,
        InstallationResolution,
        get_connector_installation_registry,
    )
    from services.secret_store import SecretStore, get_secret_store
    from services.state_dir import get_state_dir
    from services.tenant_context import DEFAULT_TENANT_ID, get_current_tenant_id
except ImportError:  # pragma: no cover
    from services.connector_installation_registry import (  # type: ignore
        ConnectorInstallation,
        ConnectorInstallationRegistry,
        InstallationResolution,
        get_connector_installation_registry,
    )
    from services.secret_store import SecretStore, get_secret_store  # type: ignore
    from services.state_dir import get_state_dir  # type: ignore
    from services.tenant_context import (  # type: ignore
        DEFAULT_TENANT_ID,
        get_current_tenant_id,
    )

logger = logging.getLogger(__name__)


@dataclass
class FeishuBinding:
    account_id: str
    app_id: str
    app_secret: str
    workspace_id: str = ""
    workspace_name: str = ""
    tenant_id: str = DEFAULT_TENANT_ID
    verification_token: str = ""
    encrypt_key: str = ""
    domain: str = "feishu"
    mode: str = "websocket"

    @property
    def installation_id(self) -> str:
        return f"feishu:{self.account_id}"

    def public_metadata(self) -> Dict[str, Any]:
        metadata = {
            "account_id": self.account_id,
            "app_id": self.app_id,
            "domain": self.domain,
            "transport_mode": self.mode,
        }
        if self.workspace_name:
            metadata["workspace_name"] = self.workspace_name
        return metadata


class FeishuInstallationManager:
    def __init__(
        self,
        config: ConnectorConfig,
        *,
        registry: Optional[ConnectorInstallationRegistry] = None,
        secret_store: Optional[SecretStore] = None,
        state_dir: Optional[str] = None,
    ):
        self.config = config
        self._state_dir = state_dir or get_state_dir()
        self._registry = registry or get_connector_installation_registry(
            state_dir=self._state_dir
        )
        self._secret_store = secret_store or get_secret_store(self._state_dir)
        self._bindings: Dict[str, FeishuBinding] = {}
        self._load_bindings()

    def _normalize_nonempty(self, value: Any, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name}_missing")
        return text

    def _normalize_optional(self, value: Any) -> str:
        return str(value or "").strip()

    def _binding_from_payload(self, raw: Dict[str, Any]) -> FeishuBinding:
        app_id = self._normalize_nonempty(raw.get("app_id"), "app_id")
        app_secret = self._normalize_nonempty(raw.get("app_secret"), "app_secret")
        account_id = self._normalize_optional(raw.get("account_id")) or app_id
        workspace_id = self._normalize_optional(raw.get("workspace_id"))
        return FeishuBinding(
            account_id=account_id,
            app_id=app_id,
            app_secret=app_secret,
            workspace_id=workspace_id,
            workspace_name=self._normalize_optional(raw.get("workspace_name")),
            tenant_id=self._normalize_optional(raw.get("tenant_id"))
            or DEFAULT_TENANT_ID,
            verification_token=self._normalize_optional(raw.get("verification_token")),
            encrypt_key=self._normalize_optional(raw.get("encrypt_key")),
            domain=self._normalize_optional(raw.get("domain")) or "feishu",
            mode=self._normalize_optional(raw.get("mode")) or self.config.feishu_mode,
        )

    def _load_bindings(self) -> None:
        bindings: List[FeishuBinding] = []
        if self.config.feishu_bindings_json:
            raw = json.loads(self.config.feishu_bindings_json)
            if not isinstance(raw, list) or not raw:
                raise ValueError("feishu_bindings_json must be a non-empty JSON list")
            bindings = [self._binding_from_payload(item or {}) for item in raw]
        elif self.config.feishu_app_id and self.config.feishu_app_secret:
            account_id = (
                self._normalize_optional(self.config.feishu_account_id)
                or self._normalize_optional(self.config.feishu_default_account_id)
                or self._normalize_optional(self.config.feishu_app_id)
            )
            if account_id:
                bindings = [
                    FeishuBinding(
                        account_id=account_id,
                        app_id=self.config.feishu_app_id,
                        app_secret=self.config.feishu_app_secret,
                        workspace_id=self._normalize_optional(
                            self.config.feishu_workspace_id
                        ),
                        workspace_name=self._normalize_optional(
                            self.config.feishu_workspace_name
                        ),
                        tenant_id=DEFAULT_TENANT_ID,
                        verification_token=self._normalize_optional(
                            self.config.feishu_verification_token
                        ),
                        encrypt_key=self._normalize_optional(
                            self.config.feishu_encrypt_key
                        ),
                        domain=self.config.feishu_domain,
                        mode=self.config.feishu_mode,
                    )
                ]
        for binding in bindings:
            if binding.account_id in self._bindings:
                raise ValueError(f"duplicate_feishu_account:{binding.account_id}")
            self._bindings[binding.account_id] = binding
            if binding.workspace_id:
                self._sync_binding(binding, reason="config_load")

    def has_bindings(self) -> bool:
        return bool(self._bindings)

    def binding_count(self) -> int:
        return len(self._bindings)

    @property
    def registry(self):
        return self._registry

    def bindings(self) -> List[FeishuBinding]:
        return list(self._bindings.values())

    def binding_configs(self) -> List[ConnectorConfig]:
        return [
            self.config_for_binding(binding.account_id) for binding in self.bindings()
        ]

    def config_for_binding(self, account_id: str) -> ConnectorConfig:
        binding = self.get_binding(account_id)
        if binding is None:
            raise ValueError(f"unknown_feishu_account:{account_id}")
        return replace(
            self.config,
            feishu_app_id=binding.app_id,
            feishu_app_secret=binding.app_secret,
            feishu_verification_token=binding.verification_token,
            feishu_encrypt_key=binding.encrypt_key,
            feishu_account_id=binding.account_id,
            feishu_workspace_id=binding.workspace_id or self.config.feishu_workspace_id,
            feishu_workspace_name=binding.workspace_name
            or self.config.feishu_workspace_name,
            feishu_domain=binding.domain,
            feishu_mode=binding.mode or self.config.feishu_mode,
        )

    def get_binding(self, account_id: str) -> Optional[FeishuBinding]:
        binding = self._bindings.get(str(account_id or "").strip())
        if binding is None:
            return None
        return replace(binding)

    def _sync_binding(
        self, binding: FeishuBinding, *, reason: str
    ) -> ConnectorInstallation:
        token_values = {"app_secret": binding.app_secret}
        if binding.verification_token:
            token_values["verification_token"] = binding.verification_token
        if binding.encrypt_key:
            token_values["encrypt_key"] = binding.encrypt_key
        inst = self._registry.upsert_installation(
            platform="feishu",
            tenant_id=binding.tenant_id,
            workspace_id=binding.workspace_id,
            installation_id=binding.installation_id,
            token_values=token_values,
            status="active",
            metadata=binding.public_metadata(),
            status_reason=reason,
        )
        return self._registry.activate_installation(
            inst.installation_id, reason=reason or "feishu_binding_ready"
        )

    def ensure_workspace_binding(
        self, account_id: str, workspace_id: str
    ) -> ConnectorInstallation:
        binding = self._bindings.get(str(account_id or "").strip())
        if binding is None:
            raise ValueError(f"unknown_feishu_account:{account_id}")
        normalized_workspace = self._normalize_nonempty(workspace_id, "workspace_id")
        if binding.workspace_id and binding.workspace_id != normalized_workspace:
            raise ValueError("feishu_workspace_mismatch")
        if binding.workspace_id == normalized_workspace:
            inst = self._registry.get_installation(binding.installation_id)
            if inst is not None:
                return inst
        binding.workspace_id = normalized_workspace
        self._bindings[binding.account_id] = binding
        return self._sync_binding(binding, reason="workspace_bound")

    def installation_id_for_account(self, account_id: str) -> str:
        return f"feishu:{str(account_id or '').strip()}"

    def _secrets_for_installation(
        self, installation: ConnectorInstallation
    ) -> Dict[str, str]:
        secrets: Dict[str, str] = {}
        for name, ref in dict(installation.token_refs or {}).items():
            value = self._secret_store.get_secret(ref, tenant_id=installation.tenant_id)
            if value:
                secrets[name] = value
        return secrets

    def resolve_binding(
        self,
        *,
        workspace_id: str = "",
        account_id: str = "",
    ) -> Tuple[InstallationResolution, Optional[FeishuBinding], Dict[str, str]]:
        normalized_workspace = self._normalize_optional(workspace_id)
        normalized_account = self._normalize_optional(account_id)

        if normalized_account:
            binding = self._bindings.get(normalized_account)
            if binding is None:
                return (
                    InstallationResolution(
                        ok=False,
                        reject_reason="missing_binding",
                        audit_code="conn_install.resolve_missing",
                        health_code="workspace_unbound",
                    ),
                    None,
                    {},
                )
            if normalized_workspace:
                try:
                    self.ensure_workspace_binding(
                        binding.account_id, normalized_workspace
                    )
                except ValueError:
                    return (
                        InstallationResolution(
                            ok=False,
                            reject_reason="tenant_mismatch",
                            audit_code="conn_install.resolve_tenant_mismatch",
                            health_code="degraded",
                        ),
                        replace(binding),
                        {},
                    )
            inst = self._registry.get_installation(binding.installation_id)
            if inst is not None and inst.workspace_id:
                resolution = self._registry.resolve_installation(
                    "feishu",
                    inst.workspace_id,
                    tenant_id=get_current_tenant_id(),
                )
                if not resolution.ok:
                    return resolution, replace(binding), {}
                return (
                    resolution,
                    replace(binding),
                    self._secrets_for_installation(resolution.installation),
                )
            return (
                InstallationResolution(
                    ok=True,
                    audit_code="conn_install.resolve_ok",
                    health_code="ok",
                ),
                replace(binding),
                {
                    "app_secret": binding.app_secret,
                    "verification_token": binding.verification_token,
                    "encrypt_key": binding.encrypt_key,
                },
            )

        if normalized_workspace:
            matching_unbound = [
                binding
                for binding in self._bindings.values()
                if not binding.workspace_id and self.binding_count() == 1
            ]
            if matching_unbound:
                inst = self.ensure_workspace_binding(
                    matching_unbound[0].account_id, normalized_workspace
                )
                return (
                    InstallationResolution(
                        ok=True,
                        installation=inst,
                        audit_code="conn_install.resolve_ok",
                        health_code="ok",
                    ),
                    replace(matching_unbound[0]),
                    self._secrets_for_installation(inst),
                )
            resolution = self._registry.resolve_installation(
                "feishu",
                normalized_workspace,
                tenant_id=get_current_tenant_id(),
            )
            if not resolution.ok or resolution.installation is None:
                return resolution, None, {}
            account_id = str(
                (resolution.installation.metadata or {}).get("account_id", "") or ""
            ).strip()
            binding = self._bindings.get(account_id)
            return (
                resolution,
                replace(binding) if binding else None,
                self._secrets_for_installation(resolution.installation),
            )

        default_account = self._normalize_optional(
            self.config.feishu_default_account_id
        )
        if default_account:
            return self.resolve_binding(account_id=default_account)
        if self.binding_count() == 1:
            only_binding = next(iter(self._bindings.values()))
            return self.resolve_binding(account_id=only_binding.account_id)
        return (
            InstallationResolution(
                ok=False,
                reject_reason="ambiguous_binding",
                audit_code="conn_install.resolve_ambiguous",
                health_code="degraded",
            ),
            None,
            {},
        )

    def resolve_inbound_binding(
        self,
        *,
        verification_token: str = "",
        workspace_id: str = "",
        account_id: str = "",
    ) -> FeishuBinding:
        normalized_token = self._normalize_optional(verification_token)
        normalized_workspace = self._normalize_optional(workspace_id)
        normalized_account = self._normalize_optional(account_id)

        if normalized_account:
            resolution, binding, _ = self.resolve_binding(
                workspace_id=normalized_workspace,
                account_id=normalized_account,
            )
            if not resolution.ok or binding is None:
                raise ValueError(resolution.reject_reason or "missing_binding")
            return binding

        candidates = list(self._bindings.values())
        if normalized_token:
            candidates = [
                binding
                for binding in candidates
                if binding.verification_token == normalized_token
            ]
            if not candidates:
                raise ValueError("invalid_verification_token")
        if normalized_workspace:
            exact = [
                binding
                for binding in candidates
                if binding.workspace_id == normalized_workspace
            ]
            if exact:
                candidates = exact
            else:
                unbound = [
                    binding for binding in candidates if not binding.workspace_id
                ]
                if len(unbound) == 1:
                    self.ensure_workspace_binding(
                        unbound[0].account_id, normalized_workspace
                    )
                    return replace(self._bindings[unbound[0].account_id])
        if len(candidates) == 1:
            binding = candidates[0]
            if normalized_workspace and binding.workspace_id:
                self.ensure_workspace_binding(binding.account_id, normalized_workspace)
            return replace(binding)
        raise ValueError("ambiguous_binding")

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

    def mark_resolution_success(self, installation_id: str, workspace_id: str) -> None:
        self._registry.update_installation_health(
            installation_id,
            health_code="ok",
            reason="workspace_resolved",
            details={"workspace_id": workspace_id},
        )

    def classify_error_health(self, error_code: str, status_code: int = 0) -> str:
        normalized = str(error_code or "").strip().lower()
        if (
            status_code in (401, 403)
            or "invalid" in normalized
            or "unauth" in normalized
        ):
            return "invalid_token"
        if "revoke" in normalized:
            return "revoked"
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
