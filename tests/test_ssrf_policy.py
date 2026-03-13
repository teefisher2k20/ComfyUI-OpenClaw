"""
Tests for S16 SSRF URL Policy.
"""

import os
import unittest
from unittest.mock import Mock, patch

from services.runtime_config import get_llm_egress_controls, validate_config_update


class TestSSRFPolicy(unittest.TestCase):

    def test_default_provider_url_allowed_always(self):
        # OpenAI default URL should pass
        updates = {"provider": "openai", "base_url": "https://api.openai.com/v1"}
        sanitized, errors = validate_config_update(updates)
        self.assertEqual(errors, [])
        self.assertEqual(sanitized["base_url"], "https://api.openai.com/v1")

    def test_local_provider_loopback_allowed(self):
        updates = {"provider": "ollama", "base_url": "http://127.0.0.1:11434"}
        sanitized, errors = validate_config_update(updates)
        self.assertEqual(errors, [])

    def test_local_provider_remote_blocked(self):
        updates = {"provider": "ollama", "base_url": "http://192.168.1.5:11434"}
        # Should fail because local providers are restricted to localhost
        sanitized, errors = validate_config_update(updates)
        self.assertTrue(len(errors) > 0)
        self.assertIn("must use localhost", errors[0])

    def test_custom_url_blocked_by_default(self):
        updates = {"provider": "custom", "base_url": "https://my-api.com/v1"}
        with patch.dict(os.environ, {}, clear=True):
            sanitized, errors = validate_config_update(updates)
            self.assertTrue(len(errors) > 0)
            self.assertIn("OPENCLAW_ALLOW_CUSTOM_BASE_URL", errors[0])

    def test_custom_url_allowed_if_enabled(self):
        updates = {
            "provider": "custom",
            "base_url": "https://google.com",  # safe public
        }
        # Avoid real DNS dependency in tests by mocking DNS resolution to a public IP.
        fake_addrinfo = [(0, 0, 0, "", ("8.8.8.8", 443))]
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_ALLOW_CUSTOM_BASE_URL": "1",
                "OPENCLAW_LLM_ALLOWED_HOSTS": "google.com",
            },
        ):
            with patch(
                "services.safe_io.socket.getaddrinfo", return_value=fake_addrinfo
            ):
                sanitized, errors = validate_config_update(updates)
                self.assertEqual(errors, [])

    def test_ssrf_private_ip_blocked(self):
        updates = {"provider": "custom", "base_url": "http://192.168.1.1/secret"}
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_ALLOW_CUSTOM_BASE_URL": "1",
                "OPENCLAW_LLM_ALLOWED_HOSTS": "192.168.1.1",
            },
        ):
            # This triggers DNS/IP check in validate_outbound_url
            # safe_io checks IPs. '192.168.1.1' is private.
            sanitized, errors = validate_config_update(updates)
            self.assertTrue(len(errors) > 0)
            self.assertIn("SRF", errors[0])  # Should contain SSRF error
            self.assertIn("private/reserved IP targets still require", errors[0])
            self.assertIn("Wildcard '*'", errors[0])

    def test_ssrf_override(self):
        updates = {"provider": "custom", "base_url": "http://192.168.1.1/secret"}
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_ALLOW_CUSTOM_BASE_URL": "1",
                "OPENCLAW_ALLOW_INSECURE_BASE_URL": "1",
                "OPENCLAW_LLM_ALLOWED_HOSTS": "192.168.1.1",
            },
        ):
            sanitized, errors = validate_config_update(updates)
            self.assertEqual(errors, [])

    def test_egress_controls_local_provider_loopback_only(self):
        controls = get_llm_egress_controls("ollama", "http://127.0.0.1:11434")
        self.assertIsNotNone(controls["allow_loopback_hosts"])
        self.assertIn("127.0.0.1", controls["allow_loopback_hosts"])
        self.assertIn("localhost", controls["allow_hosts"])

    def test_egress_controls_custom_provider_does_not_get_loopback_exception(self):
        controls = get_llm_egress_controls("custom", "http://127.0.0.1:11434")
        self.assertIsNone(controls["allow_loopback_hosts"])
        self.assertNotIn("127.0.0.1", controls["allow_hosts"])


if __name__ == "__main__":
    unittest.main()
