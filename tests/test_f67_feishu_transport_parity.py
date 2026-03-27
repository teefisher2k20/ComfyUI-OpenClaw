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
from connector.platforms.feishu_long_connection import FeishuLongConnectionClient
from connector.platforms.feishu_webhook import (
    FeishuWebhookServer,
    _build_multipart_form,
)
from services.connector_installation_registry import ConnectorInstallationRegistry
from services.secret_store import SecretStore


def _event_payload(
    *,
    text: str = "/status",
    chat_type: str = "p2p",
    mentions=None,
    tenant_key: str = "tenant-1",
):
    return {
        "schema": "2.0",
        "header": {
            "event_id": "fe-evt-1",
            "event_type": "im.message.receive_v1",
            "tenant_key": tenant_key,
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
                "chat_id": "oc_dm_1" if chat_type == "p2p" else "oc_group_1",
                "chat_type": chat_type,
                "message_type": "text",
                "content": json.dumps({"text": text}),
                "mentions": mentions or [],
                "root_id": "om_root_1",
            },
        },
    }


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def read(self):
        return json.dumps(self._payload).encode("utf-8")


class TestF67FeishuTransportParity(unittest.IsolatedAsyncioTestCase):
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
        self.router = MagicMock()
        self.router.handle = AsyncMock(return_value=CommandResponse(text="OK"))
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

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def test_webhook_challenge_returns_challenge(self):
        response = await self.server.handle_event(
            _FakeRequest(
                {
                    "type": "url_verification",
                    "challenge": "abc123",
                    "token": "verify-token",
                }
            )
        )
        self.assertEqual(response.status, 200)
        self.assertIn("abc123", response.text)

    async def test_dm_message_routes_through_router(self):
        self.server._send_reply = AsyncMock()
        await self.server.process_event_payload(_event_payload())
        self.router.handle.assert_called_once()
        req = self.router.handle.call_args[0][0]
        self.assertEqual(req.platform, "feishu")
        self.assertEqual(req.sender_id, "u_sender")
        self.assertEqual(req.workspace_id, "tenant-1")
        self.assertEqual(req.thread_id, "om_root_1")
        self.server._send_reply.assert_awaited_once()

    async def test_group_message_requires_bot_mention(self):
        self.server._bot_open_id = "ou_bot"
        self.server._send_reply = AsyncMock()
        await self.server.process_event_payload(_event_payload(chat_type="group"))
        self.router.handle.assert_not_called()
        self.server._send_reply.assert_not_awaited()

    async def test_group_message_with_bot_mention_routes(self):
        self.server._bot_open_id = "ou_bot"
        self.server._send_reply = AsyncMock()
        mentions = [
            {"key": "@_user_1", "name": "Bot", "id": {"open_id": "ou_bot"}},
        ]
        await self.server.process_event_payload(
            _event_payload(
                chat_type="group",
                text="@_user_1 /status",
                mentions=mentions,
            )
        )
        self.router.handle.assert_called_once()
        req = self.router.handle.call_args[0][0]
        self.assertEqual(req.text, "/status")

    async def test_long_connection_reuses_event_path(self):
        client = FeishuLongConnectionClient(
            self.config,
            self.router,
            installation_manager=self.manager,
        )
        client._send_reply = AsyncMock()
        await client._handle_long_connection_event(_event_payload())
        self.router.handle.assert_called_once()

    async def test_long_connection_start_skips_without_sdk(self):
        client = FeishuLongConnectionClient(
            self.config,
            self.router,
            installation_manager=self.manager,
        )
        with patch(
            "connector.platforms.feishu_long_connection._import_feishu_sdk",
            return_value=None,
        ):
            await client.start()
        self.assertIsNone(client._ws_client)

    async def test_token_fetch_uses_safe_request_json_and_caches(self):
        with patch(
            "connector.platforms.feishu_webhook.safe_request_json",
            return_value={
                "code": 0,
                "tenant_access_token": "tok_1",
                "expire": 3600,
            },
        ) as mock_safe:
            token_one = await self.server._get_tenant_access_token()
            token_two = await self.server._get_tenant_access_token()
        self.assertEqual(token_one, "tok_1")
        self.assertEqual(token_two, "tok_1")
        self.assertEqual(mock_safe.call_count, 1)
        kwargs = mock_safe.call_args.kwargs
        self.assertEqual(kwargs["method"], "POST")
        self.assertIn("/tenant_access_token/internal", kwargs["url"])

    async def test_send_reply_uses_safe_request_json(self):
        with (
            patch.object(
                self.server, "_get_tenant_access_token", AsyncMock(return_value="tok_1")
            ),
            patch(
                "connector.platforms.feishu_webhook.safe_request_json",
                return_value={"code": 0, "data": {}},
            ) as mock_safe,
        ):
            await self.server.send_message(
                "oc_dm_1",
                "hello",
                delivery_context={"workspace_id": "tenant-1"},
            )
        kwargs = mock_safe.call_args.kwargs
        self.assertEqual(kwargs["method"], "POST")
        self.assertIn("/open-apis/im/v1/messages", kwargs["url"])
        self.assertEqual(kwargs["json_body"]["msg_type"], "text")
        self.assertEqual(kwargs["json_body"]["receive_id"], "oc_dm_1")

    async def test_send_image_uses_safe_request_json_for_upload_and_send(self):
        with (
            patch.object(
                self.server, "_get_tenant_access_token", AsyncMock(return_value="tok_1")
            ),
            patch(
                "connector.platforms.feishu_webhook.safe_request_json",
                side_effect=[
                    {"code": 0, "data": {"image_key": "img_1"}},
                    {"code": 0, "data": {}},
                ],
            ) as mock_safe,
        ):
            await self.server.send_image(
                "oc_dm_1",
                b"png-bytes",
                delivery_context={"thread_id": "om_1", "workspace_id": "tenant-1"},
            )
        self.assertEqual(mock_safe.call_count, 2)
        upload_kwargs = mock_safe.call_args_list[0].kwargs
        send_kwargs = mock_safe.call_args_list[1].kwargs
        self.assertIn("/open-apis/im/v1/images", upload_kwargs["url"])
        self.assertTrue(
            upload_kwargs["content_type"].startswith("multipart/form-data; boundary=")
        )
        self.assertIn(b"png-bytes", upload_kwargs["raw_body"])
        self.assertIn("/open-apis/im/v1/messages/om_1/reply", send_kwargs["url"])
        self.assertEqual(send_kwargs["json_body"]["msg_type"], "image")


class TestF67FeishuMultipartEncoding(unittest.TestCase):
    def test_multipart_form_contains_fields_and_file(self):
        body, content_type = _build_multipart_form(
            fields={"image_type": "message"},
            file_field="image",
            filename="sample.png",
            file_bytes=b"png-bytes",
            file_content_type="image/png",
        )
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="image_type"', body)
        self.assertIn(b"message", body)
        self.assertIn(b'name="image"; filename="sample.png"', body)
        self.assertIn(b"Content-Type: image/png", body)
        self.assertIn(b"png-bytes", body)


if __name__ == "__main__":
    unittest.main()
