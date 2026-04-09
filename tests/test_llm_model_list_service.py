import unittest
from unittest.mock import patch

from services.llm_model_list import (
    ModelListTarget,
    fetch_remote_model_list,
    resolve_model_list_target,
)


class LlmModelListServiceTests(unittest.TestCase):
    @patch("services.providers.keys.requires_api_key", return_value=False)
    @patch("services.providers.keys.get_api_key_for_provider", return_value=None)
    def test_resolve_target_uses_runtime_base_url(self, _mock_key, _mock_requires_key):
        target = resolve_model_list_target(
            provider_override="custom",
            effective={"provider": "custom", "base_url": "https://custom.example/v1"},
            tenant_id="tenant-a",
        )

        self.assertEqual(target.provider, "custom")
        self.assertEqual(target.base_url, "https://custom.example/v1")
        self.assertEqual(
            target.cache_key,
            ("tenant-a", "custom", "https://custom.example/v1"),
        )

    @patch("services.providers.keys.requires_api_key", return_value=False)
    @patch("services.providers.keys.get_api_key_for_provider", return_value=None)
    def test_resolve_target_normalizes_legacy_ollama_root_url(
        self, _mock_key, _mock_requires_key
    ):
        target = resolve_model_list_target(
            provider_override="ollama",
            effective={"provider": "ollama", "base_url": "http://127.0.0.1:11434"},
            tenant_id="default",
        )

        self.assertEqual(target.provider, "ollama")
        self.assertEqual(target.base_url, "http://127.0.0.1:11434/v1")
        self.assertEqual(
            target.cache_key,
            ("ollama", "http://127.0.0.1:11434/v1"),
        )

    @patch("services.safe_io.safe_request_json")
    def test_fetch_remote_model_list_builds_auth_header(self, mock_safe_request):
        mock_safe_request.return_value = {"data": [{"id": "gpt-4o-mini"}]}

        target = ModelListTarget(
            provider="openai",
            base_url="https://api.openai.com/v1",
            tenant_id="default",
            cache_key=("openai", "https://api.openai.com/v1"),
            api_key="sk-test",
            requires_api_key=True,
        )

        models = fetch_remote_model_list(
            target,
            {"allow_hosts": {"api.openai.com"}},
            pack_version="0.1.0",
            allow_insecure_base_url=False,
        )

        self.assertEqual(models, ["gpt-4o-mini"])
        self.assertEqual(
            mock_safe_request.call_args.kwargs["headers"]["Authorization"],
            "Bearer sk-test",
        )


if __name__ == "__main__":
    unittest.main()
