"""
Unit Tests for Connector Config (F29).
"""

import os
import unittest
from unittest.mock import patch

from connector.config import load_config


class TestConnectorConfig(unittest.TestCase):
    def test_basic_load(self):
        with patch.dict(
            os.environ, {"OPENCLAW_CONNECTOR_URL": "http://localhost:5555"}
        ):
            cfg = load_config()
            self.assertEqual(cfg.openclaw_url, "http://localhost:5555")

    def test_telegram_allowlist(self):
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS": "123, 456, abc ",
                "OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS": "-100, 200",
            },
        ):
            cfg = load_config()
            self.assertEqual(cfg.telegram_allowed_users, [123, 456])
            self.assertEqual(cfg.telegram_allowed_chats, [-100, 200])

    def test_discord_allowlist(self):
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS": "u1,u2,,",
                "OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS": "c1",
            },
        ):
            cfg = load_config()
            self.assertEqual(cfg.discord_allowed_users, ["u1", "u2"])
            self.assertEqual(cfg.discord_allowed_channels, ["c1"])

    def test_feishu_config(self):
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_CONNECTOR_FEISHU_APP_ID": "cli_xxx",
                "OPENCLAW_CONNECTOR_FEISHU_APP_SECRET": "sec_xxx",
                "OPENCLAW_CONNECTOR_FEISHU_VERIFICATION_TOKEN": "verify-token",
                "OPENCLAW_CONNECTOR_FEISHU_ACCOUNT_ID": "acct-default",
                "OPENCLAW_CONNECTOR_FEISHU_DEFAULT_ACCOUNT_ID": "acct-default",
                "OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_ID": "tenant-alpha",
                "OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_NAME": "Alpha Workspace",
                "OPENCLAW_CONNECTOR_FEISHU_BINDINGS_JSON": '[{"account_id":"acct-extra","workspace_id":"tenant-beta","app_id":"cli_extra","app_secret":"sec_extra"}]',
                "OPENCLAW_CONNECTOR_FEISHU_CALLBACK_PATH": "/feishu/cards",
                "OPENCLAW_CONNECTOR_FEISHU_ALLOWED_USERS": "u1,u2",
                "OPENCLAW_CONNECTOR_FEISHU_ALLOWED_CHATS": "oc_a,oc_b",
                "OPENCLAW_CONNECTOR_FEISHU_DOMAIN": "lark",
                "OPENCLAW_CONNECTOR_FEISHU_MODE": "webhook",
                "OPENCLAW_CONNECTOR_FEISHU_REQUIRE_MENTION": "false",
                "OPENCLAW_CONNECTOR_FEISHU_REPLY_IN_THREAD": "false",
            },
            clear=False,
        ):
            cfg = load_config()
            self.assertEqual(cfg.feishu_app_id, "cli_xxx")
            self.assertEqual(cfg.feishu_app_secret, "sec_xxx")
            self.assertEqual(cfg.feishu_verification_token, "verify-token")
            self.assertEqual(cfg.feishu_account_id, "acct-default")
            self.assertEqual(cfg.feishu_default_account_id, "acct-default")
            self.assertEqual(cfg.feishu_workspace_id, "tenant-alpha")
            self.assertEqual(cfg.feishu_workspace_name, "Alpha Workspace")
            self.assertIn("acct-extra", cfg.feishu_bindings_json)
            self.assertEqual(cfg.feishu_callback_path, "/feishu/cards")
            self.assertEqual(cfg.feishu_allowed_users, ["u1", "u2"])
            self.assertEqual(cfg.feishu_allowed_chats, ["oc_a", "oc_b"])
            self.assertEqual(cfg.feishu_domain, "lark")
            self.assertEqual(cfg.feishu_mode, "webhook")
            self.assertFalse(cfg.feishu_require_mention)
            self.assertFalse(cfg.feishu_reply_in_thread)


if __name__ == "__main__":
    unittest.main()
