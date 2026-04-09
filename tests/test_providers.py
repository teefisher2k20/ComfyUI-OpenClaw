"""
Tests for LLM Provider Catalog.
R16: Provider catalog, keys, and adapter tests.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

from services.providers.catalog import (
    DEFAULT_MODEL_BY_PROVIDER,
    DEFAULT_PROVIDER,
    PROVIDER_CATALOG,
    ProviderInfo,
    ProviderType,
    get_loopback_host_aliases,
    get_provider_info,
    is_local_provider,
    is_loopback_host,
    list_providers,
    normalize_provider_base_url,
)
from services.providers.keys import (
    get_all_configured_keys,
    get_api_key_for_provider,
    mask_api_key,
    requires_api_key,
)


class TestProviderCatalog(unittest.TestCase):

    def test_catalog_has_required_providers(self):
        """Test that catalog contains all required providers."""
        required = [
            "openai",
            "anthropic",
            "openrouter",
            "gemini",
            "groq",
            "deepseek",
            "xai",
            "ollama",
            "lmstudio",
            "custom",
        ]
        for provider in required:
            self.assertIn(provider, PROVIDER_CATALOG)

    def test_provider_info_structure(self):
        """Test that all providers have required fields."""
        for name, info in PROVIDER_CATALOG.items():
            self.assertIsInstance(info.name, str)
            self.assertIsInstance(info.base_url, str)
            self.assertIsInstance(info.api_type, ProviderType)
            self.assertIsInstance(info.supports_vision, bool)

    def test_get_provider_info(self):
        """Test provider info lookup."""
        info = get_provider_info("anthropic")
        self.assertIsNotNone(info)
        self.assertEqual(info.api_type, ProviderType.ANTHROPIC)

        info = get_provider_info("openai")
        self.assertIsNotNone(info)
        self.assertEqual(info.api_type, ProviderType.OPENAI_COMPAT)

    def test_get_provider_info_case_insensitive(self):
        """Test that provider lookup is case-insensitive."""
        info = get_provider_info("ANTHROPIC")
        self.assertIsNotNone(info)

    def test_get_provider_info_unknown(self):
        """Test that unknown provider returns None."""
        info = get_provider_info("unknown_provider")
        self.assertIsNone(info)

    def test_list_providers(self):
        """Test listing all providers."""
        providers = list_providers()
        self.assertIsInstance(providers, list)
        self.assertIn("anthropic", providers)
        self.assertIn("openai", providers)

    def test_default_models_exist(self):
        """Test that default models are defined for all providers."""
        for provider in PROVIDER_CATALOG.keys():
            self.assertIn(provider, DEFAULT_MODEL_BY_PROVIDER)

    def test_local_provider_detection(self):
        """Local providers should be identified explicitly."""
        self.assertTrue(is_local_provider("ollama"))
        self.assertTrue(is_local_provider("lmstudio"))
        self.assertFalse(is_local_provider("openai"))

    def test_ollama_default_base_url_uses_v1_openai_compat_prefix(self):
        self.assertEqual(
            PROVIDER_CATALOG["ollama"].base_url,
            "http://127.0.0.1:11434/v1",
        )

    def test_ollama_root_base_url_normalizes_to_v1(self):
        self.assertEqual(
            normalize_provider_base_url("ollama", "http://127.0.0.1:11434"),
            "http://127.0.0.1:11434/v1",
        )
        self.assertEqual(
            normalize_provider_base_url("ollama", "http://127.0.0.1:11434/v1"),
            "http://127.0.0.1:11434/v1",
        )

    def test_loopback_helpers(self):
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertFalse(is_loopback_host("api.openai.com"))
        self.assertEqual(
            get_loopback_host_aliases("localhost"),
            {"localhost", "127.0.0.1", "::1"},
        )


class TestProviderKeys(unittest.TestCase):

    def test_mask_api_key_short(self):
        """Test masking short keys."""
        self.assertEqual(mask_api_key("abc"), "****")
        self.assertEqual(mask_api_key("12345678"), "****")

    def test_mask_api_key_long(self):
        """Test masking longer keys."""
        masked = mask_api_key("sk-1234567890abcdef")
        self.assertEqual(masked, "sk-1...cdef")

    def test_mask_api_key_empty(self):
        """Test masking empty/None."""
        self.assertEqual(mask_api_key(""), "")

    def test_requires_api_key_cloud(self):
        """Test that cloud providers require keys."""
        self.assertTrue(requires_api_key("openai"))
        self.assertTrue(requires_api_key("anthropic"))

    def test_requires_api_key_local(self):
        """Test that local providers don't require keys."""
        self.assertFalse(requires_api_key("ollama"))
        self.assertFalse(requires_api_key("lmstudio"))

    def test_get_api_key_provider_specific(self):
        """Test provider-specific key lookup."""
        with patch.dict(os.environ, {"MOLTBOT_OPENAI_API_KEY": "test-openai-key"}):
            key = get_api_key_for_provider("openai")
            self.assertEqual(key, "test-openai-key")

    def test_get_api_key_legacy_fallback(self):
        """Test fallback to legacy key."""
        with patch.dict(os.environ, {"MOLTBOT_LLM_API_KEY": "legacy-key"}, clear=True):
            key = get_api_key_for_provider("openai")
            self.assertEqual(key, "legacy-key")

    def test_get_api_key_provider_takes_precedence(self):
        """Test that provider-specific key takes precedence over legacy."""
        with patch.dict(
            os.environ,
            {
                "MOLTBOT_OPENAI_API_KEY": "specific-key",
                "MOLTBOT_LLM_API_KEY": "legacy-key",
            },
        ):
            key = get_api_key_for_provider("openai")
            self.assertEqual(key, "specific-key")

    def test_get_all_configured_keys_no_secrets(self):
        """Test that get_all_configured_keys never returns full keys."""
        with patch.dict(os.environ, {"MOLTBOT_OPENAI_API_KEY": "sk-secretkey12345"}):
            info = get_all_configured_keys()
            # Should never contain the full key
            for provider_id, data in info.items():
                if data.get("masked"):
                    self.assertNotEqual(data["masked"], "sk-secretkey12345")
                    self.assertIn("...", data["masked"])


if __name__ == "__main__":
    unittest.main()
