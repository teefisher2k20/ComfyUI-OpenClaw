"""
S11 secret-provider chain tests.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from services.providers.keys import get_all_configured_keys, get_api_key_for_provider
from services.secret_store import get_secret_store


class TestS11SecretProviderChain(unittest.TestCase):
    def setUp(self):
        self._orig_env = dict(os.environ)
        self._tmpdir = tempfile.mkdtemp()
        # Keep environment deterministic per test.
        for key in list(os.environ.keys()):
            if key.startswith("OPENCLAW_") or key.startswith("MOLTBOT_"):
                del os.environ[key]
        get_secret_store(state_dir=self._tmpdir).clear_all()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_env_precedence_over_onepassword_and_store(self):
        os.environ["OPENCLAW_OPENAI_API_KEY"] = "sk-env-priority"
        os.environ["OPENCLAW_1PASSWORD_ENABLED"] = "1"
        os.environ["OPENCLAW_1PASSWORD_ALLOWED_COMMANDS"] = "op"
        os.environ["OPENCLAW_1PASSWORD_VAULT"] = "vlt"

        store = get_secret_store(state_dir=self._tmpdir)
        store.set_secret("openai", "sk-store")

        with patch("services.secret_providers.subprocess.run") as mock_run:
            key = get_api_key_for_provider("openai")

        self.assertEqual(key, "sk-env-priority")
        mock_run.assert_not_called()

    def test_onepassword_lookup_success(self):
        os.environ["OPENCLAW_1PASSWORD_ENABLED"] = "1"
        os.environ["OPENCLAW_1PASSWORD_ALLOWED_COMMANDS"] = "op"
        os.environ["OPENCLAW_1PASSWORD_VAULT"] = "teamvault"

        with patch("services.secret_providers.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R",
                (),
                {"returncode": 0, "stdout": "sk-op-123\n", "stderr": ""},
            )()
            key = get_api_key_for_provider("openai")

        self.assertEqual(key, "sk-op-123")
        args = mock_run.call_args.args[0]
        self.assertEqual(args[:2], ["op", "read"])
        self.assertIn("op://teamvault/openclaw/openai/api_key", args)

    def test_onepassword_allowlist_fail_closed_falls_back_to_store(self):
        os.environ["OPENCLAW_1PASSWORD_ENABLED"] = "1"
        os.environ["OPENCLAW_1PASSWORD_ALLOWED_COMMANDS"] = "safeop"
        os.environ["OPENCLAW_1PASSWORD_CMD"] = "op"
        os.environ["OPENCLAW_1PASSWORD_VAULT"] = "teamvault"

        store = get_secret_store(state_dir=self._tmpdir)
        store.set_secret("openai", "sk-store-only")

        with patch("services.secret_providers.subprocess.run") as mock_run:
            key = get_api_key_for_provider("openai")

        self.assertEqual(key, "sk-store-only")
        mock_run.assert_not_called()

    def test_onepassword_failure_falls_back_to_generic_store(self):
        os.environ["OPENCLAW_1PASSWORD_ENABLED"] = "1"
        os.environ["OPENCLAW_1PASSWORD_ALLOWED_COMMANDS"] = "op"
        os.environ["OPENCLAW_1PASSWORD_VAULT"] = "teamvault"

        store = get_secret_store(state_dir=self._tmpdir)
        store.set_secret("generic", "sk-generic-store")

        with patch("services.secret_providers.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R",
                (),
                {"returncode": 1, "stdout": "", "stderr": "unauthorized"},
            )()
            key = get_api_key_for_provider("openai")

        self.assertEqual(key, "sk-generic-store")
        self.assertGreaterEqual(mock_run.call_count, 1)

    def test_onepassword_error_log_does_not_leak_secret_value(self):
        os.environ["OPENCLAW_1PASSWORD_ENABLED"] = "1"
        os.environ["OPENCLAW_1PASSWORD_ALLOWED_COMMANDS"] = "op"
        os.environ["OPENCLAW_1PASSWORD_VAULT"] = "teamvault"

        leaked = "sk-should-not-appear"
        with patch("services.secret_providers.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R",
                (),
                {"returncode": 1, "stdout": "", "stderr": f"bad key {leaked}"},
            )()
            with self.assertLogs(
                "ComfyUI-OpenClaw.services.secret_providers", level="WARNING"
            ) as logs:
                key = get_api_key_for_provider("openai")

        self.assertIsNone(key)
        self.assertNotIn(leaked, "\n".join(logs.output))

    def test_configured_keys_reports_onepassword_source_without_value(self):
        os.environ["OPENCLAW_1PASSWORD_ENABLED"] = "1"
        os.environ["OPENCLAW_1PASSWORD_ALLOWED_COMMANDS"] = "op"
        os.environ["OPENCLAW_1PASSWORD_VAULT"] = "teamvault"

        def _resolver(*_args, **_kwargs):
            return type(
                "R", (), {"returncode": 0, "stdout": "sk-op-xyz", "stderr": ""}
            )()

        with patch("services.secret_providers.subprocess.run", side_effect=_resolver):
            info = get_all_configured_keys()

        self.assertTrue(info["openai"]["configured"])
        self.assertEqual(info["openai"]["source"], "onepassword")
        self.assertIsNone(info["openai"]["masked"])


if __name__ == "__main__":
    unittest.main()
