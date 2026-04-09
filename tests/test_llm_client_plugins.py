"""
Tests for LLMClient plugin integration (R23 runtime wiring).
"""

import unittest
from unittest.mock import AsyncMock, patch

from services.llm_client import LLMClient


class TestLLMClientPluginIntegration(unittest.TestCase):
    """Test plugin hooks in LLMClient."""

    @patch("services.llm_client.plugin_manager")
    @patch("services.llm_client.PLUGINS_AVAILABLE", True)
    @patch("services.runtime_config.get_effective_config")
    def test_model_alias_resolution_on_init(self, mock_config, mock_pm):
        """Model alias should be resolved during initialization."""
        mock_config.return_value = (
            {
                "provider": "openai",
                "model": "gpt4",  # Alias
                "base_url": "https://api.openai.com/v1",
                "timeout_sec": 120,
                "max_retries": 3,
            },
            None,
        )

        # Mock async plugin execution returning resolved model
        mock_pm.execute_first = AsyncMock(return_value="gpt-4")

        client = LLMClient()

        # Verify plugin was called
        mock_pm.execute_first.assert_called_once()
        call_args = mock_pm.execute_first.call_args
        self.assertEqual(call_args[0][0], "model.resolve")

        # Verify model was resolved
        self.assertEqual(client.model, "gpt-4")

    @patch("services.llm_client.plugin_manager")
    @patch("services.llm_client.PLUGINS_AVAILABLE", True)
    @patch("services.runtime_config.get_effective_config")
    @patch("services.llm_client.get_api_key_for_provider")
    @patch("services.llm_client.openai_compat.make_request")
    def test_param_clamping_applied(self, mock_request, mock_key, mock_config, mock_pm):
        """Parameter transforms should be applied via plugins."""
        mock_config.return_value = (
            {
                "provider": "openai",
                "model": "gpt-4",
                "base_url": "https://api.openai.com/v1",
                "timeout_sec": 120,
                "max_retries": 3,
            },
            None,
        )
        mock_key.return_value = "sk-test"

        # Mock plugins
        mock_pm.execute_first = AsyncMock(return_value="gpt-4")  # Model resolution
        mock_pm.execute_sequential = AsyncMock(
            return_value={"temperature": 1.0, "max_tokens": 4096}
        )  # Clamped
        mock_pm.execute_parallel = AsyncMock()

        mock_request.return_value = {"text": "test", "raw": {}}

        client = LLMClient()
        client.complete(
            system="test",
            user_message="test",
            temperature=1.5,  # Invalid - should be clamped to 1.0
        )

        # Verify plugin was called
        self.assertEqual(mock_pm.execute_sequential.call_count, 1)
        call_args = mock_pm.execute_sequential.call_args
        self.assertEqual(call_args[0][0], "llm.params")

        # Verify clamped param was used (check openai_compat.make_request call)
        request_call = mock_request.call_args
        self.assertEqual(request_call[1]["temperature"], 1.0)

    @patch("services.llm_client.plugin_manager")
    @patch("services.llm_client.PLUGINS_AVAILABLE", True)
    @patch("services.runtime_config.get_effective_config")
    @patch("services.llm_client.get_api_key_for_provider")
    @patch("services.llm_client.openai_compat.make_request")
    def test_audit_hook_called(self, mock_request, mock_key, mock_config, mock_pm):
        """Audit hook should be called for observability."""
        mock_config.return_value = (
            {
                "provider": "openai",
                "model": "gpt-4",
                "base_url": "https://api.openai.com/v1",
                "timeout_sec": 120,
                "max_retries": 3,
            },
            None,
        )
        mock_key.return_value = "sk-test"

        mock_pm.execute_first = AsyncMock(return_value="gpt-4")
        mock_pm.execute_sequential = AsyncMock(
            return_value={"temperature": 0.7, "max_tokens": 4096}
        )
        mock_pm.execute_parallel = AsyncMock()

        mock_request.return_value = {"text": "test", "raw": {}}

        client = LLMClient()
        client.complete(
            system="test",
            user_message="test",
            trace_id="trace-123",
        )

        # Verify audit hook was called
        self.assertEqual(mock_pm.execute_parallel.call_count, 1)
        call_args = mock_pm.execute_parallel.call_args
        self.assertEqual(call_args[0][0], "llm.audit_request")

        # Verify context had trace_id
        ctx = call_args[0][1]
        self.assertEqual(ctx.trace_id, "trace-123")

    @patch("services.llm_client.PLUGINS_AVAILABLE", False)
    @patch("services.runtime_config.get_effective_config")
    @patch("services.llm_client.get_api_key_for_provider")
    @patch("services.llm_client.openai_compat.make_request")
    def test_plugins_unavailable_graceful_degradation(
        self, mock_request, mock_key, mock_config
    ):
        """Should work gracefully when plugins unavailable."""
        mock_config.return_value = (
            {
                "provider": "openai",
                "model": "gpt-4",
                "base_url": "https://api.openai.com/v1",
                "timeout_sec": 120,
                "max_retries": 3,
            },
            None,
        )
        mock_key.return_value = "sk-test"
        mock_request.return_value = {"text": "test", "raw": {}}

        client = LLMClient()
        result = client.complete(
            system="test",
            user_message="test",
        )

        # Should succeed without plugins
        self.assertEqual(result["text"], "test")

    @patch("services.llm_client.PLUGINS_AVAILABLE", False)
    @patch("services.runtime_config.get_effective_config")
    @patch("services.effective_config.get_effective_config")
    @patch("services.llm_client.requires_api_key", return_value=False)
    @patch("services.llm_client.get_api_key_for_provider", return_value=None)
    @patch("services.llm_client.openai_compat.make_request")
    def test_ollama_root_base_url_is_normalized_before_openai_compat_request(
        self,
        mock_request,
        _mock_key,
        _mock_requires_key,
        mock_effective_config_facade,
        mock_runtime_config,
    ):
        config_payload = (
            {
                "provider": "ollama",
                "model": "llama3.2",
                "base_url": "http://127.0.0.1:11434",
                "timeout_sec": 120,
                "max_retries": 3,
            },
            None,
        )
        mock_runtime_config.return_value = config_payload
        mock_effective_config_facade.return_value = config_payload
        mock_request.return_value = {"text": "test", "raw": {}}

        client = LLMClient()
        client.complete(system="test", user_message="test")

        self.assertEqual(
            mock_request.call_args.kwargs["base_url"],
            "http://127.0.0.1:11434/v1",
        )


if __name__ == "__main__":
    unittest.main()
