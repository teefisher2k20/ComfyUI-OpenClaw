"""
F57 -- Slack Transport Parity Contract.
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connector.config import ConnectorConfig
from connector.contract import CommandResponse
from connector.platforms.slack_socket_mode import SlackSocketModeClient


class TestF57SlackTransportParity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = ConnectorConfig()
        self.config.slack_bot_token = "xoxb-mock"
        self.config.slack_app_token = "xapp-mock"
        self.config.slack_signing_secret = "secret"

        self.router = MagicMock()
        self.router.handle = AsyncMock(return_value=CommandResponse(text=""))
        self.client = SlackSocketModeClient(self.config, self.router)

    async def test_socket_mode_routes_message(self):
        payload = {
            "token": "verification_token",
            "team_id": "T123",
            "api_app_id": "A123",
            "event": {
                "type": "message",
                "text": "/status",
                "user": "U123",
                "channel": "C123",
                "ts": "1234.5678",
            },
            "type": "event_callback",
            "event_id": "Ev123",
            "event_time": 12345678,
        }

        await self.client.process_event_payload(payload)
        self.router.handle.assert_called_once()
        req = self.router.handle.call_args[0][0]
        self.assertEqual(req.platform, "slack")
        self.assertEqual(req.text, "/status")
        self.assertEqual(req.sender_id, "U123")

    async def test_socket_mode_ignores_bot_message(self):
        self.client._bot_user_id = "U_BOT"
        payload = {
            "type": "event_callback",
            "event_id": "EvBot",
            "event": {
                "type": "message",
                "text": "Self reply",
                "user": "U_BOT",
                "channel": "C123",
                "ts": "1234.9999",
            },
        }

        await self.client.process_event_payload(payload)
        self.router.handle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
