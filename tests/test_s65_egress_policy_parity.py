"""
S65 egress policy parity tests.

Verify that critical outbound paths use safe_io wrappers instead of direct
urllib/requests calls.
"""

import ast
import os
import sys
import unittest
from unittest.mock import patch

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.providers import anthropic, openai_compat


class TestS65EgressPolicyParity(unittest.TestCase):
    """S65: critical provider paths must call safe_request_json."""

    def test_anthropic_uses_safe_io(self):
        with patch("services.providers.anthropic.safe_request_json") as mock_safe:
            mock_safe.return_value = {"content": [{"type": "text", "text": "Hello"}]}

            result = anthropic.make_request(
                base_url="https://api.anthropic.com",
                api_key="sk-test",
                messages=[{"role": "user", "content": "Hi"}],
                model="claude-3",
            )

            self.assertEqual(result["text"], "Hello")
            mock_safe.assert_called_once()
            kwargs = mock_safe.call_args.kwargs
            self.assertIn("allow_hosts", kwargs)
            self.assertIn("allow_any_public_host", kwargs)
            self.assertIn("allow_loopback_hosts", kwargs)
            self.assertIn("allow_insecure_base_url", kwargs)

    def test_openai_compat_uses_safe_io(self):
        with patch("services.providers.openai_compat.safe_request_json") as mock_safe:
            mock_safe.return_value = {
                "choices": [{"message": {"content": "Hello"}}],
                "model": "gpt-4",
            }

            result = openai_compat.make_request(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                messages=[{"role": "user", "content": "Hi"}],
                model="gpt-4",
            )

            self.assertEqual(result["text"], "Hello")
            mock_safe.assert_called_once()
            kwargs = mock_safe.call_args.kwargs
            self.assertIn("allow_hosts", kwargs)
            self.assertIn("allow_any_public_host", kwargs)
            self.assertIn("allow_loopback_hosts", kwargs)
            self.assertIn("allow_insecure_base_url", kwargs)


class TestS65ModelListEgressConvergence(unittest.TestCase):
    """S65: model-list fetch path must be safe_io-based."""

    def test_model_list_handler_no_urllib_urlopen(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "api", "config.py")
        with open(config_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename="api/config.py")
        urlopen_found = any(
            isinstance(node, ast.Attribute) and node.attr == "urlopen"
            for node in ast.walk(tree)
        )

        self.assertFalse(
            urlopen_found,
            "api/config.py still contains urllib.request.urlopen; S65 requires safe_io.",
        )

    def test_model_list_handler_imports_safe_request_json(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "api", "config.py")
        with open(config_path, "r", encoding="utf-8") as f:
            source = f.read()

        self.assertIn(
            "safe_request_json",
            source,
            "api/config.py must import safe_request_json for S65 compliance.",
        )


class TestS65StaticGuardNoCriticalUrllib(unittest.TestCase):
    """S65 static guard: critical egress modules must not use direct urlopen."""

    CRITICAL_MODULES = [
        os.path.join("services", "providers", "anthropic.py"),
        os.path.join("services", "providers", "openai_compat.py"),
        os.path.join("services", "callback_delivery.py"),
        os.path.join("services", "control_plane_adapter.py"),
        os.path.join("api", "config.py"),
    ]

    def test_no_urllib_urlopen_in_critical_modules(self):
        project_root = os.path.join(os.path.dirname(__file__), "..")
        violations = []

        for rel_path in self.CRITICAL_MODULES:
            full_path = os.path.join(project_root, rel_path)
            if not os.path.isfile(full_path):
                continue

            with open(full_path, "r", encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source, filename=rel_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "urlopen":
                    violations.append(f"{rel_path}:{node.lineno} direct urllib urlopen")

        self.assertEqual(
            violations,
            [],
            (
                "S65 violation: direct urllib.request.urlopen in critical modules: "
                f"{violations}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
