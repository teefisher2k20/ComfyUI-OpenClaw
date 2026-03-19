import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connector.config import ConnectorConfig
from connector.contract import CommandResponse
from connector.platforms.slack_installation_manager import SlackInstallationManager
from connector.platforms.slack_webhook import SlackWebhookServer
from services.connector_installation_registry import ConnectorInstallationRegistry
from services.secret_store import SecretStore


class TestF58SlackOAuthInstallations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = self.tmpdir.name
        self.secret_store = SecretStore(state_dir=self.state_dir)
        self.registry = ConnectorInstallationRegistry(
            state_dir=self.state_dir,
            secret_store=self.secret_store,
        )
        self.config = ConnectorConfig()
        self.config.public_base_url = "https://connector.example.com"
        self.config.slack_signing_secret = "signing-secret"
        self.config.slack_client_id = "client-id"
        self.config.slack_client_secret = "client-secret"
        self.config.slack_bot_token = "xoxb-legacy"
        self.manager = SlackInstallationManager(
            self.config,
            registry=self.registry,
            secret_store=self.secret_store,
            state_dir=self.state_dir,
        )

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    def _oauth_payload(self, token: str, *, workspace_id: str = "T1") -> dict:
        return {
            "ok": True,
            "app_id": "A123",
            "access_token": token,
            "scope": "chat:write,files:write",
            "bot_user_id": "U_BOT_1",
            "team": {"id": workspace_id, "name": "Workspace One"},
            "authed_user": {"id": "U_INSTALLER"},
            "token_type": "bot",
        }

    async def test_oauth_state_single_use_and_workspace_binding(self):
        state = self.manager.issue_install_state()
        self.assertTrue(self.manager.consume_install_state(state))
        self.assertFalse(self.manager.consume_install_state(state))

        inst = self.manager.upsert_from_oauth_payload(self._oauth_payload("xoxb-first"))
        self.assertEqual(inst.workspace_id, "T1")
        self.assertEqual(inst.status, "active")

        rotated = self.manager.upsert_from_oauth_payload(
            self._oauth_payload("xoxb-rotated")
        )
        self.assertEqual(rotated.installation_id, inst.installation_id)

        resolution, tokens = self.manager.resolve_workspace_tokens("T1")
        self.assertTrue(resolution.ok)
        self.assertEqual(tokens["bot_token"], "xoxb-rotated")

        with open(
            os.path.join(self.state_dir, "connector_installations.json"),
            "r",
            encoding="utf-8",
        ) as fh:
            raw = fh.read()
        self.assertNotIn("xoxb-rotated", raw)

    async def test_workspace_bound_reply_uses_installation_token(self):
        self.manager.upsert_from_oauth_payload(self._oauth_payload("xoxb-workspace"))

        router = MagicMock()
        router.handle = AsyncMock(return_value=CommandResponse(text="Done"))
        server = SlackWebhookServer(self.config, router)
        server._installation_manager = self.manager

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_session.__aenter__.return_value = mock_session
            mock_session.post.return_value.__aenter__.return_value.status = 200
            mock_session.post.return_value.__aenter__.return_value.json = AsyncMock(
                return_value={"ok": True}
            )
            mock_session.post.return_value.__aenter__.return_value.text = AsyncMock(
                return_value="OK"
            )

            payload = {
                "type": "event_callback",
                "team_id": "T1",
                "authorizations": [{"user_id": "U_BOT_1", "team_id": "T1"}],
                "event_id": "EvF58-1",
                "event": {
                    "type": "message",
                    "text": "/status",
                    "user": "U_SENDER",
                    "channel": "D_DM",
                    "ts": "1700000000.001",
                },
            }

            await server.process_event_payload(payload)

        routed = router.handle.call_args[0][0]
        self.assertEqual(routed.workspace_id, "T1")
        self.assertEqual(routed.thread_id, "1700000000.001")
        headers = mock_session.post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer xoxb-workspace")

    async def test_tokens_revoked_event_marks_installation_invalid(self):
        self.manager.upsert_from_oauth_payload(self._oauth_payload("xoxb-workspace"))
        router = MagicMock()
        router.handle = AsyncMock(return_value=CommandResponse(text="Done"))
        server = SlackWebhookServer(self.config, router)
        server._installation_manager = self.manager

        payload = {
            "type": "event_callback",
            "team_id": "T1",
            "event": {
                "type": "tokens_revoked",
            },
        }
        await server.process_event_payload(payload)

        resolution = self.registry.resolve_installation("slack", "T1")
        self.assertFalse(resolution.ok)
        self.assertEqual(resolution.health_code, "invalid_token")
