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
from connector.platforms.feishu_webhook import FeishuDeliveryTarget, FeishuWebhookServer
from services.connector_installation_registry import ConnectorInstallationRegistry
from services.secret_store import SecretStore


def _event_payload():
    return {
        "schema": "2.0",
        "header": {
            "event_id": "fe-evt-f69-1",
            "event_type": "im.message.receive_v1",
            "tenant_key": "tenant-1",
            "token": "verify-token",
        },
        "event": {
            "sender": {
                "sender_id": {
                    "user_id": "u_sender",
                    "open_id": "ou_sender",
                }
            },
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_dm_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "/approvals"}),
                "mentions": [],
                "root_id": "om_root_1",
            },
        },
    }


class TestF69FeishuCallbacks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = self.tmpdir.name
        self.secret_store = SecretStore(state_dir=self.state_dir)
        self.registry = ConnectorInstallationRegistry(
            state_dir=self.state_dir,
            secret_store=self.secret_store,
        )
        self.config = ConnectorConfig()
        self.config.feishu_app_id = "cli_test"
        self.config.feishu_app_secret = "sec_test"
        self.config.feishu_account_id = "acct-default"
        self.config.feishu_default_account_id = "acct-default"
        self.config.feishu_verification_token = "verify-token"
        self.config.feishu_workspace_id = "tenant-1"
        self.config.feishu_mode = "webhook"
        self.config.feishu_callback_path = "/feishu/callback"
        self.router = MagicMock()
        self.router.handle = AsyncMock(return_value=CommandResponse(text="OK"))
        self.router._is_admin = MagicMock(return_value=False)
        self.router._is_trusted = MagicMock(return_value=False)
        self.manager = FeishuInstallationManager(
            self.config,
            registry=self.registry,
            secret_store=self.secret_store,
            state_dir=self.state_dir,
        )
        self.server = FeishuWebhookServer(
            self.config,
            self.router,
            installation_manager=self.manager,
        )
        self.binding = self.manager.get_binding("acct-default")

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    def _callback_body(
        self,
        *,
        button,
        actor_user_id="admin-user",
        actor_open_id="ou_admin",
    ):
        value = self.server._build_card_button_value(
            button,
            target=FeishuDeliveryTarget(
                channel_id="oc_dm_1",
                reply_to_message_id="om_card_1",
                workspace_id="tenant-1",
                account_id="acct-default",
            ),
            binding=self.binding,
            signing_secret=self.binding.app_secret,
        )
        return {
            "header": {"tenant_key": "tenant-1"},
            "event": {
                "open_chat_id": "oc_dm_1",
                "open_message_id": "om_card_1",
                "operator": {
                    "operator_id": {
                        "user_id": actor_user_id,
                        "open_id": actor_open_id,
                    }
                },
                "action": {"value": value},
            },
        }

    async def test_approvals_response_uses_interactive_card(self):
        self.router.handle = AsyncMock(
            return_value=CommandResponse(
                text="Pending Approvals (1):\n- apr_1 [pending]",
                buttons=[
                    {
                        "label": "Approve apr_1",
                        "value": "/approve apr_1",
                        "action_type": "approval.approve",
                        "approval_id": "apr_1",
                        "style": "primary",
                    }
                ],
            )
        )
        with (
            patch.object(
                self.server, "_get_tenant_access_token", AsyncMock(return_value="tok_1")
            ),
            patch(
                "connector.platforms.feishu_webhook.safe_request_json",
                return_value={"code": 0, "data": {}},
            ) as mock_safe,
        ):
            await self.server.process_event_payload(_event_payload())

        kwargs = mock_safe.call_args.kwargs
        self.assertEqual(kwargs["json_body"]["msg_type"], "interactive")
        card = json.loads(kwargs["json_body"]["content"])
        action = card["elements"][1]["actions"][0]
        self.assertEqual(action["text"]["content"], "Approve apr_1")
        self.assertEqual(
            action["value"]["callback_envelope"]["action_type"], "approval.approve"
        )
        self.assertEqual(action["value"]["payload"]["command"], "/approve apr_1")

    async def test_admin_callback_routes_and_duplicate_is_deduped(self):
        self.router._is_admin = MagicMock(return_value=True)
        self.router._is_trusted = MagicMock(return_value=True)
        self.router.handle = AsyncMock(
            return_value=CommandResponse(text="[Approved] apr_1")
        )
        body = self._callback_body(
            button={
                "label": "Approve apr_1",
                "value": "/approve apr_1",
                "action_type": "approval.approve",
                "approval_id": "apr_1",
                "style": "primary",
            }
        )

        first = await self.server.process_callback_payload(body)
        second = await self.server.process_callback_payload(body)

        self.assertTrue(first["ok"])
        self.assertEqual(first["decision_code"], "cb_accept_admin")
        req = self.router.handle.call_args.args[0]
        self.assertEqual(req.text, "/approve apr_1")
        self.assertTrue(second["ok"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.router.handle.await_count, 1)

    async def test_run_callback_degrades_to_approval_for_untrusted_actor(self):
        self.router._is_admin = MagicMock(return_value=False)
        self.router._is_trusted = MagicMock(return_value=False)
        self.router.handle = AsyncMock(
            return_value=CommandResponse(text="[Approval Requested]")
        )
        body = self._callback_body(
            button={
                "label": "Run template",
                "value": "/run template_x prompt=city",
                "action_type": "command.run",
            },
            actor_user_id="u_untrusted",
            actor_open_id="ou_untrusted",
        )

        response = await self.server.process_callback_payload(body)

        self.assertTrue(response["ok"])
        self.assertEqual(response["decision_code"], "cb_require_approval")
        req = self.router.handle.call_args.args[0]
        self.assertIn("--approval", req.text)

    async def test_stale_callback_is_rejected(self):
        body = self._callback_body(
            button={
                "label": "Approve apr_1",
                "value": "/approve apr_1",
                "action_type": "approval.approve",
                "approval_id": "apr_1",
            }
        )
        envelope = body["event"]["action"]["value"]["callback_envelope"]
        envelope["timestamp"] = envelope["timestamp"] - 1000

        with self.assertRaisesRegex(ValueError, "timestamp_out_of_window"):
            await self.server.process_callback_payload(body)

    async def test_invalid_signature_is_rejected(self):
        body = self._callback_body(
            button={
                "label": "Approve apr_1",
                "value": "/approve apr_1",
                "action_type": "approval.approve",
                "approval_id": "apr_1",
            }
        )
        body["event"]["action"]["value"]["callback_envelope"]["signature"] = "bad"

        with self.assertRaisesRegex(ValueError, "signature_mismatch"):
            await self.server.process_callback_payload(body)


if __name__ == "__main__":
    unittest.main()
