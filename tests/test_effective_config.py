import unittest
from unittest.mock import patch

import config as config_module
from services import effective_config


class EffectiveConfigFacadeTests(unittest.TestCase):
    @patch("services.effective_config.get_effective_config")
    def test_model_uses_config_only_when_provider_matches(self, mock_get_effective):
        mock_get_effective.return_value = (
            {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
            },
            {},
        )

        self.assertEqual(
            effective_config.get_effective_llm_model("openai"),
            "gpt-4o-mini",
        )
        self.assertNotEqual(
            effective_config.get_effective_llm_model("anthropic"),
            "gpt-4o-mini",
        )

    @patch("services.effective_config.get_effective_config")
    def test_base_url_uses_catalog_default_when_provider_differs(
        self, mock_get_effective
    ):
        mock_get_effective.return_value = (
            {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "base_url": "https://custom-openai.example/v1",
            },
            {},
        )

        self.assertEqual(
            effective_config.get_effective_llm_base_url("openai"),
            "https://custom-openai.example/v1",
        )
        self.assertNotEqual(
            effective_config.get_effective_llm_base_url("anthropic"),
            "https://custom-openai.example/v1",
        )

    @patch("config.get_effective_llm_api_key", return_value="sk-effective")
    def test_config_get_api_key_uses_effective_facade(self, mock_get_key):
        self.assertEqual(config_module.get_api_key(), "sk-effective")
        mock_get_key.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
