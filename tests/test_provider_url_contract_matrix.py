import unittest
from unittest.mock import patch

from services.llm_model_list import (
    _MODEL_LIST_CACHE,
    ModelListTarget,
    fetch_remote_model_list,
    resolve_model_list_target,
)
from services.providers import anthropic, openai_compat
from services.providers.catalog import PROVIDER_CATALOG


# IMPORTANT: keep built-in provider URL shapes centralized here. The BF08 Ollama
# bug survived because endpoint expectations were fragmented and one low-mock
# lane accepted both valid and invalid paths. This matrix is the single seam
# that must fail if any provider drifts to the wrong endpoint contract.
OPENAI_COMPAT_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}

ANTHROPIC_DEFAULTS = {
    "anthropic": "https://api.anthropic.com",
    "antigravity_proxy": "http://127.0.0.1:8080",
}

CUSTOM_OPENAI_COMPAT_BASE_URL = "https://gateway.example.com/tenant-a/v1"


class ProviderUrlContractMatrixTests(unittest.TestCase):
    def setUp(self):
        _MODEL_LIST_CACHE.clear()

    def test_catalog_default_base_url_matrix(self):
        for provider, expected_base_url in OPENAI_COMPAT_DEFAULTS.items():
            with self.subTest(provider=provider):
                self.assertEqual(PROVIDER_CATALOG[provider].base_url, expected_base_url)

        for provider, expected_base_url in ANTHROPIC_DEFAULTS.items():
            with self.subTest(provider=provider):
                self.assertEqual(PROVIDER_CATALOG[provider].base_url, expected_base_url)

    @patch("services.providers.openai_compat.safe_request_json")
    def test_openai_compat_chat_endpoint_matrix(self, mock_safe_request):
        mock_safe_request.return_value = {"choices": [{"message": {"content": "ok"}}]}

        rows = [
            *OPENAI_COMPAT_DEFAULTS.items(),
            ("custom", CUSTOM_OPENAI_COMPAT_BASE_URL),
        ]

        for provider, base_url in rows:
            with self.subTest(provider=provider):
                mock_safe_request.reset_mock()
                openai_compat.make_request(
                    base_url=base_url,
                    api_key="sk-test" if provider != "ollama" else None,
                    messages=[{"role": "user", "content": "hi"}],
                    model="test-model",
                    allow_any_public_host=True,
                )
                self.assertEqual(
                    mock_safe_request.call_args.kwargs["url"],
                    f"{base_url.rstrip('/')}/chat/completions",
                )

    @patch("services.safe_io.safe_request_json")
    def test_model_list_endpoint_matrix(self, mock_safe_request):
        mock_safe_request.return_value = {"data": [{"id": "model-a"}]}

        rows = [
            *OPENAI_COMPAT_DEFAULTS.items(),
            ("custom", CUSTOM_OPENAI_COMPAT_BASE_URL),
        ]

        for provider, base_url in rows:
            with self.subTest(provider=provider):
                mock_safe_request.reset_mock()
                target = ModelListTarget(
                    provider=provider,
                    base_url=base_url,
                    tenant_id="default",
                    cache_key=(provider, base_url),
                    api_key=None,
                    requires_api_key=False,
                )
                models = fetch_remote_model_list(
                    target,
                    {"allow_hosts": set(), "allow_any_public_host": True},
                    pack_version="0.8.5",
                    allow_insecure_base_url=False,
                )
                self.assertEqual(models, ["model-a"])
                self.assertEqual(
                    mock_safe_request.call_args.kwargs["url"],
                    f"{base_url.rstrip('/')}/models",
                )

    @patch("services.providers.anthropic.safe_request_json")
    def test_anthropic_message_endpoint_matrix(self, mock_safe_request):
        mock_safe_request.return_value = {"content": [{"type": "text", "text": "ok"}]}

        for provider, base_url in ANTHROPIC_DEFAULTS.items():
            with self.subTest(provider=provider):
                mock_safe_request.reset_mock()
                anthropic.make_request(
                    base_url=base_url,
                    api_key="sk-ant-test",
                    messages=[{"role": "user", "content": "hi"}],
                    model="test-model",
                    allow_any_public_host=True,
                )
                self.assertEqual(
                    mock_safe_request.call_args.kwargs["url"],
                    f"{base_url.rstrip('/')}/v1/messages",
                )

    @patch("services.providers.keys.requires_api_key", return_value=False)
    @patch("services.providers.keys.get_api_key_for_provider", return_value=None)
    def test_runtime_base_url_resolution_matrix_preserves_provider_contracts(
        self, _mock_key, _mock_requires_key
    ):
        rows = [
            ("ollama", "http://127.0.0.1:11434", "http://127.0.0.1:11434/v1"),
            ("ollama", "http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
            ("lmstudio", "http://localhost:1234/v1", "http://localhost:1234/v1"),
            ("custom", CUSTOM_OPENAI_COMPAT_BASE_URL, CUSTOM_OPENAI_COMPAT_BASE_URL),
        ]

        for provider, configured_base_url, expected_base_url in rows:
            with self.subTest(provider=provider, base_url=configured_base_url):
                target = resolve_model_list_target(
                    provider_override=provider,
                    effective={
                        "provider": provider,
                        "base_url": configured_base_url,
                    },
                    tenant_id="default",
                )
                self.assertEqual(target.base_url, expected_base_url)


if __name__ == "__main__":
    unittest.main()
