import json
import time
import unittest
from unittest.mock import MagicMock, patch

try:
    from aiohttp import web  # type: ignore
except Exception:  # pragma: no cover
    web = None  # type: ignore

if web is not None:
    from api.config import (
        _MODEL_LIST_CACHE,
        _extract_models_from_payload,
        llm_models_handler,
    )


@unittest.skipIf(web is None, "aiohttp not installed")
class TestModelListAPI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _MODEL_LIST_CACHE.clear()

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("services.safe_io.safe_request_json")
    async def test_handler_default_allowlist_allows_builtin_hosts(
        self,
        mock_safe_request,
        mock_require_admin,
        mock_rate_limit,
        mock_get_key,
        mock_get_config,
    ):
        """
        Built-in providers should work out-of-the-box without requiring
        OPENCLAW_LLM_ALLOWED_HOSTS to be set.
        """
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = (
            {"provider": "gemini", "base_url": ""},
            {},
        )
        mock_get_key.return_value = "sk-test"

        # S65: safe_request_json returns parsed dict directly
        mock_safe_request.return_value = {"data": [{"id": "gemini-2.0-flash"}]}

        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        with patch.dict("os.environ", {}, clear=True):
            resp = await llm_models_handler(request)

        self.assertEqual(resp.status, 200, f"Expected 200 OK, got {resp.status}")
        data = json.loads(resp.body)
        self.assertTrue(data["ok"])
        self.assertIn("gemini-2.0-flash", data["models"])

        # Verify safe_request_json was called (S65 compliance)
        mock_safe_request.assert_called_once()
        call_kwargs = mock_safe_request.call_args.kwargs
        # Should have passed allow_hosts (not allow_any)
        self.assertIn("allow_hosts", call_kwargs)
        self.assertFalse(call_kwargs.get("allow_any_public_host", False))
        self.assertIsNone(call_kwargs.get("json_body"))

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("services.providers.keys.requires_api_key")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("services.safe_io.safe_request_json")
    async def test_handler_local_provider_no_api_key_allowed(
        self,
        mock_safe_request,
        mock_require_admin,
        mock_rate_limit,
        mock_requires_key,
        mock_get_key,
        mock_get_config,
    ):
        """Local providers should not be blocked by API-key gate."""
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = (
            {"provider": "ollama", "base_url": "http://127.0.0.1:11434"},
            {},
        )
        mock_requires_key.return_value = False
        mock_get_key.return_value = None
        mock_safe_request.return_value = {"models": [{"id": "gemma3:4b"}]}

        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        resp = await llm_models_handler(request)
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.body)
        self.assertTrue(data["ok"])
        self.assertIn("gemma3:4b", data["models"])
        self.assertIn(("ollama", "http://127.0.0.1:11434/v1"), _MODEL_LIST_CACHE)
        self.assertEqual(
            mock_safe_request.call_args.kwargs["url"],
            "http://127.0.0.1:11434/v1/models",
        )

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("services.safe_io.safe_request_json")
    async def test_handler_success(
        self,
        mock_safe_request,
        mock_require_admin,
        mock_rate_limit,
        mock_get_key,
        mock_get_config,
    ):
        # Setup mocks
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = (
            {"provider": "openai", "base_url": "https://api.openai.com"},
            {},
        )
        mock_get_key.return_value = "sk-test"

        # S65: safe_request_json returns parsed dict directly
        mock_safe_request.return_value = {"data": [{"id": "gpt-4o"}]}

        # Request
        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        # Execute
        resp = await llm_models_handler(request)

        # Assert
        self.assertEqual(resp.status, 200, f"Expected 200 OK, got {resp.status}")
        data = json.loads(resp.body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["models"], ["gpt-4o"])
        self.assertFalse(data["cached"])

        # Verify cache use composite key (provider, base_url)
        self.assertIn(("openai", "https://api.openai.com"), _MODEL_LIST_CACHE)

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("services.safe_io.safe_request_json")
    async def test_handler_cached_fallback(
        self,
        mock_safe_request,
        mock_require_admin,
        mock_rate_limit,
        mock_get_key,
        mock_get_config,
    ):
        # Setup cache with specific base_url - make it STALE (older than TTL)
        cache_key = ("openai", "https://api.openai.com")
        _MODEL_LIST_CACHE[cache_key] = (time.time() - 7200, ["cached-model"])

        # Setup mocks
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = (
            {"provider": "openai", "base_url": "https://api.openai.com"},
            {},
        )
        mock_get_key.return_value = "sk-test"

        # S65: Simulate network failure via safe_request_json
        mock_safe_request.side_effect = Exception("Network fail")

        # Request
        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        # Execute
        resp = await llm_models_handler(request)

        # Assert - Should return 200 with cached=True and warning
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["models"], ["cached-model"])
        self.assertTrue(data["cached"])
        self.assertIn("Using cached list", data.get("warning", ""))

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("services.safe_io.safe_request_json")
    @patch("services.safe_io.validate_outbound_url")
    async def test_handler_base_url_override(
        self,
        mock_validate_url,
        mock_safe_request,
        mock_require_admin,
        mock_rate_limit,
        mock_get_key,
        mock_get_config,
    ):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        # Override base_url in config
        mock_get_config.return_value = (
            {"provider": "custom", "base_url": "http://custom-host:8080/v1"},
            {},
        )
        mock_get_key.return_value = "sk-custom"

        # S65: safe_request_json returns parsed dict directly
        mock_safe_request.return_value = {"models": [{"id": "custom-model"}]}

        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        resp = await llm_models_handler(request)

        self.assertEqual(resp.status, 200)
        # Check cache key uses custom url
        self.assertIn(("custom", "http://custom-host:8080/v1"), _MODEL_LIST_CACHE)
        kwargs = mock_safe_request.call_args.kwargs
        self.assertFalse(kwargs.get("allow_any_public_host", False))
        self.assertIsNotNone(kwargs.get("allow_hosts"))

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("services.safe_io.safe_request_json")
    @patch("services.safe_io.validate_outbound_url")
    async def test_handler_allow_any_public_host_controls_are_consistent(
        self,
        mock_validate_url,
        mock_safe_request,
        mock_require_admin,
        mock_rate_limit,
        mock_get_key,
        mock_get_config,
    ):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = (
            {"provider": "custom", "base_url": "https://example.com/v1"},
            {},
        )
        mock_get_key.return_value = "sk-custom"
        mock_safe_request.return_value = {"models": [{"id": "x"}]}

        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        with patch.dict("os.environ", {"OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "1"}):
            resp = await llm_models_handler(request)

        self.assertEqual(resp.status, 200)
        validate_kwargs = mock_validate_url.call_args.kwargs
        request_kwargs = mock_safe_request.call_args.kwargs
        self.assertTrue(validate_kwargs["allow_any_public_host"])
        self.assertTrue(request_kwargs["allow_any_public_host"])
        self.assertIsNone(validate_kwargs["allow_hosts"])
        self.assertIsNone(request_kwargs["allow_hosts"])

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("services.providers.catalog.get_provider_info")
    async def test_handler_unsupported_provider(
        self,
        mock_get_info,
        mock_require_admin,
        mock_rate_limit,
        mock_get_key,
        mock_get_config,
    ):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = ({"provider": "anthropic"}, {})

        # Mock provider info as NOT OpenAI-compat
        mock_info = MagicMock()
        from services.providers.catalog import ProviderType

        mock_info.api_type = ProviderType.ANTHROPIC  # Not OpenAI Compat
        mock_get_info.return_value = mock_info

        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        resp = await llm_models_handler(request)
        self.assertEqual(resp.status, 400)  # Should be 400 Bad Request
        data = json.loads(resp.body)
        self.assertIn("only supported for OpenAI-compatible", data["error"])

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("services.safe_io.safe_request_json")
    @patch("services.safe_io.validate_outbound_url")
    async def test_handler_insecure_override_reaches_request_time(
        self,
        mock_validate_url,
        mock_safe_request,
        mock_require_admin,
        mock_rate_limit,
        mock_get_key,
        mock_get_config,
    ):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = (
            {"provider": "openai", "base_url": "http://192.168.2.27:8000/v1"},
            {},
        )
        mock_get_key.return_value = "sk-test"
        mock_safe_request.return_value = {"data": [{"id": "model-a"}]}

        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        with patch.dict("os.environ", {"OPENCLAW_ALLOW_INSECURE_BASE_URL": "1"}):
            resp = await llm_models_handler(request)

        self.assertEqual(resp.status, 200)
        self.assertTrue(mock_validate_url.call_args.kwargs["allow_insecure_base_url"])
        self.assertTrue(mock_safe_request.call_args.kwargs["allow_insecure_base_url"])

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    async def test_handler_ssrf_blocked(
        self, mock_require_admin, mock_rate_limit, mock_get_key, mock_get_config
    ):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = (
            {"provider": "openai", "base_url": "http://169.254.169.254"},
            {},
        )

        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        # Simulate SSRF failure
        with patch(
            "services.safe_io.validate_outbound_url", side_effect=ValueError("Blocked")
        ):
            resp = await llm_models_handler(request)

        self.assertEqual(resp.status, 403)
        data = json.loads(resp.body)
        self.assertIn("SSRF policy blocked", data["error"])
        self.assertIn("private/reserved IP targets still require", data["error"])
        self.assertIn("Wildcard '*'", data["error"])

    @patch("api.config.get_effective_config")
    @patch("services.providers.keys.get_api_key_for_provider")
    @patch("api.config.check_rate_limit")
    @patch("api.config.require_admin_token")
    @patch("api.config.is_loopback_client")
    async def test_handler_remote_access_denied(
        self,
        mock_is_loopback,
        mock_require_admin,
        mock_rate_limit,
        mock_get_key,
        mock_get_config,
    ):
        mock_rate_limit.return_value = True
        # Admin token is valid, but request is remote
        mock_require_admin.return_value = (True, None)
        mock_get_config.return_value = ({"provider": "openai"}, {})

        # Simulate Remote IP
        request = MagicMock()
        request.query = {}
        request.remote = "192.168.1.50"

        # Mock loopback check to return False (remote)
        mock_is_loopback.return_value = False

        # Ensure ENV doesn't allow remote
        with patch.dict("os.environ", {}, clear=True):
            resp = await llm_models_handler(request)

        self.assertEqual(resp.status, 403)
        data = json.loads(resp.body)
        self.assertIn("Remote admin access denied", data["error"])
