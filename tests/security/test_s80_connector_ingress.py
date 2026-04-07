import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connector.config import ConnectorConfig
from connector.platforms.feishu_webhook import FeishuWebhookServer
from connector.platforms.slack_webhook import SlackWebhookServer
from connector.platforms.wechat_webhook import XMLBudgetExceeded, parse_wechat_xml


class TestS80WeChatIngressHardening(unittest.TestCase):
    def test_parse_wechat_xml_rejects_dtd_and_entity_declarations(self):
        payload = (
            b'<!DOCTYPE xml [<!ENTITY boom "boom">]>'
            b"<xml><Content>&boom;</Content></xml>"
        )

        with self.assertRaisesRegex(XMLBudgetExceeded, "DTD/entity"):
            parse_wechat_xml(payload)


class TestS80SlackIngressHardening(unittest.IsolatedAsyncioTestCase):
    async def test_oauth_callback_does_not_echo_raw_exception(self):
        config = ConnectorConfig()
        router = MagicMock()
        server = SlackWebhookServer(config, router)
        server._installation_manager = MagicMock()
        server._installation_manager.can_handle_oauth.return_value = True
        server._installation_manager.consume_install_state.return_value = True
        server._installation_manager.exchange_code = AsyncMock(
            side_effect=RuntimeError("token leak: xoxb-secret")
        )

        request = MagicMock()
        request.query = {"state": "state-1", "code": "code-1"}

        with patch(
            "connector.platforms.slack_webhook._import_aiohttp_web",
            return_value=(None, None),
        ):
            response = await server.handle_oauth_callback(request)

        self.assertEqual(response.status, 502)
        self.assertIn("failed", response.text.lower())
        self.assertNotIn("xoxb-secret", response.text)


class TestS80FeishuIngressHardening(unittest.IsolatedAsyncioTestCase):
    def _server(self) -> FeishuWebhookServer:
        return FeishuWebhookServer(ConnectorConfig(), MagicMock())

    async def test_handle_event_uses_bounded_error_code(self):
        server = self._server()
        request = MagicMock()
        request.read = AsyncMock(return_value=b"{}")

        with (
            patch(
                "connector.platforms.feishu_webhook._import_aiohttp_web",
                return_value=(None, None),
            ),
            patch.object(server, "_verify_request_token", return_value=True),
            patch.object(
                server,
                "process_event_payload",
                AsyncMock(side_effect=ValueError("stack trace: secret=abc123")),
            ),
        ):
            response = await server.handle_event(request)

        self.assertEqual(response.status, 400)
        self.assertEqual(response.text, "event_rejected")

    async def test_handle_callback_uses_bounded_error_code(self):
        server = self._server()
        request = MagicMock()
        request.read = AsyncMock(return_value=b"{}")

        with (
            patch(
                "connector.platforms.feishu_webhook._import_aiohttp_web",
                return_value=(None, None),
            ),
            patch.object(
                server,
                "process_callback_payload",
                AsyncMock(side_effect=ValueError("stack trace: secret=abc123")),
            ),
        ):
            response = await server.handle_callback(request)

        self.assertEqual(response.status, 403)
        body = json.loads(response.body.decode("utf-8"))
        self.assertEqual(body["error"], "callback_rejected")
        self.assertNotIn("abc123", response.text)
