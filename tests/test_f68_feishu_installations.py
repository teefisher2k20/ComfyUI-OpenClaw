import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connector.config import ConnectorConfig
from connector.contract import CommandResponse
from connector.platforms.feishu_installation_manager import FeishuInstallationManager
from connector.platforms.feishu_webhook import FeishuWebhookServer
from services.connector_installation_registry import ConnectorInstallationRegistry
from services.secret_store import SecretStore
from services.tenant_context import tenant_scope


def _event_payload(*, workspace_id: str, verification_token: str) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"evt-{workspace_id}",
            "event_type": "im.message.receive_v1",
            "tenant_key": workspace_id,
            "token": verification_token,
        },
        "event": {
            "sender": {"sender_id": {"user_id": "u_sender", "open_id": "ou_sender"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_dm_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "/status"}),
                "mentions": [],
                "root_id": "om_root_1",
            },
        },
    }


class TestF68FeishuInstallations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = self.tmpdir.name
        self.secret_store = SecretStore(state_dir=self.state_dir)
        self.registry = ConnectorInstallationRegistry(
            state_dir=self.state_dir,
            secret_store=self.secret_store,
        )
        self.config = ConnectorConfig()
        self.config.feishu_mode = "websocket"
        self.config.feishu_bindings_json = json.dumps(
            [
                {
                    "account_id": "acct-alpha",
                    "workspace_id": "tenant-alpha",
                    "workspace_name": "Alpha",
                    "app_id": "cli_alpha",
                    "app_secret": "sec_alpha",
                    "verification_token": "verify-alpha",
                    "domain": "feishu",
                    "tenant_id": "tenant-a",
                },
                {
                    "account_id": "acct-beta",
                    "workspace_id": "tenant-beta",
                    "workspace_name": "Beta",
                    "app_id": "cli_beta",
                    "app_secret": "sec_beta",
                    "verification_token": "verify-beta",
                    "domain": "lark",
                    "tenant_id": "tenant-b",
                },
            ]
        )

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    def _manager(self) -> FeishuInstallationManager:
        return FeishuInstallationManager(
            self.config,
            registry=self.registry,
            secret_store=self.secret_store,
            state_dir=self.state_dir,
        )

    async def test_bindings_json_syncs_registry_and_diagnostics(self):
        manager = self._manager()
        self.assertEqual(manager.binding_count(), 2)

        installations = self.registry.list_installations(platform="feishu")
        self.assertEqual(len(installations), 2)
        self.assertEqual(installations[0].status, "active")
        self.assertEqual(
            self.registry.diagnostics()["installation_count"],
            2,
        )

        store_path = os.path.join(self.state_dir, "connector_installations.json")
        with open(store_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn("sec_alpha", raw)
        self.assertNotIn("sec_beta", raw)

    async def test_resolve_workspace_binding_and_default_account(self):
        self.config.feishu_default_account_id = "acct-beta"
        manager = self._manager()

        resolution, binding, secrets = manager.resolve_binding(
            workspace_id="tenant-alpha"
        )
        self.assertTrue(resolution.ok)
        self.assertEqual(binding.account_id, "acct-alpha")
        self.assertEqual(secrets["app_secret"], "sec_alpha")

        default_resolution, default_binding, default_secrets = manager.resolve_binding()
        self.assertTrue(default_resolution.ok)
        self.assertEqual(default_binding.account_id, "acct-beta")
        self.assertEqual(default_secrets["app_secret"], "sec_beta")

    async def test_resolve_binding_rejects_ambiguous_default(self):
        manager = self._manager()
        resolution, binding, secrets = manager.resolve_binding()
        self.assertFalse(resolution.ok)
        self.assertEqual(resolution.reject_reason, "ambiguous_binding")
        self.assertIsNone(binding)
        self.assertEqual(secrets, {})

    async def test_multi_tenant_resolution_rejects_mismatch(self):
        manager = self._manager()
        with patch.dict(
            os.environ,
            {"OPENCLAW_MULTI_TENANT_ENABLED": "1"},
            clear=False,
        ):
            with tenant_scope("tenant-b"):
                resolution, binding, _ = manager.resolve_binding(
                    workspace_id="tenant-alpha"
                )
        self.assertFalse(resolution.ok)
        self.assertEqual(resolution.reject_reason, "tenant_mismatch")
        self.assertIsNone(binding)

    async def test_webhook_routes_with_bound_account_metadata(self):
        manager = self._manager()
        router = MagicMock()
        router.handle = AsyncMock(return_value=CommandResponse(text="OK"))
        server = FeishuWebhookServer(
            self.config,
            router,
            installation_manager=manager,
        )
        server._send_reply = AsyncMock()
        server._bot_open_ids[manager.installation_id_for_account("acct-beta")] = (
            "ou_bot_beta"
        )

        await server.process_event_payload(
            _event_payload(
                workspace_id="tenant-beta",
                verification_token="verify-beta",
            )
        )

        req = router.handle.call_args[0][0]
        self.assertEqual(req.workspace_id, "tenant-beta")
        self.assertEqual(req.metadata["account_id"], "acct-beta")
        resolution = self.registry.resolve_installation("feishu", "tenant-beta")
        self.assertTrue(resolution.ok)
        self.assertEqual(
            resolution.installation.metadata.get("account_id"),
            "acct-beta",
        )


if __name__ == "__main__":
    unittest.main()
