"""
Tests for Webhook Auth Module.
S2: ChatOps/webhook auth verification tests.
"""

import hashlib
import hmac
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

# Import to insure module is loaded for patching
import services.idempotency_store
from services.webhook_auth import (
    constant_time_compare,
    get_auth_summary,
    is_auth_configured,
    require_auth,
    verify_bearer,
    verify_hmac,
)


class MockRequest:
    """Mock aiohttp request for testing."""

    def __init__(self, headers: dict = None):
        self.headers = headers or {}


class TestConstantTimeCompare(unittest.TestCase):

    def test_equal_strings(self):
        """Test that equal strings return True."""
        self.assertTrue(constant_time_compare("abc", "abc"))

    def test_unequal_strings(self):
        """Test that unequal strings return False."""
        self.assertFalse(constant_time_compare("abc", "def"))

    def test_empty_strings(self):
        """Test that empty strings are equal."""
        self.assertTrue(constant_time_compare("", ""))

    def test_different_lengths(self):
        """Test that different length strings return False."""
        self.assertFalse(constant_time_compare("abc", "abcd"))


class TestVerifyBearer(unittest.TestCase):

    def test_valid_bearer(self):
        """Test valid bearer token."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_BEARER_TOKEN": "secret123"}):
            request = MockRequest(headers={"Authorization": "Bearer secret123"})
            valid, error = verify_bearer(request)
            self.assertTrue(valid)
            self.assertEqual(error, "")

    def test_invalid_bearer(self):
        """Test invalid bearer token."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_BEARER_TOKEN": "secret123"}):
            request = MockRequest(headers={"Authorization": "Bearer wrong"})
            valid, error = verify_bearer(request)
            self.assertFalse(valid)
            self.assertEqual(error, "invalid_token")

    def test_missing_header(self):
        """Test missing Authorization header."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_BEARER_TOKEN": "secret123"}):
            request = MockRequest(headers={})
            valid, error = verify_bearer(request)
            self.assertFalse(valid)
            self.assertEqual(error, "missing_authorization_header")

    def test_wrong_scheme(self):
        """Test wrong auth scheme."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_BEARER_TOKEN": "secret123"}):
            request = MockRequest(headers={"Authorization": "Basic abc"})
            valid, error = verify_bearer(request)
            self.assertFalse(valid)
            self.assertEqual(error, "invalid_auth_scheme")

    def test_not_configured(self):
        """Test when bearer token is not configured."""
        with patch.dict(os.environ, {}, clear=True):
            request = MockRequest(headers={"Authorization": "Bearer secret123"})
            valid, error = verify_bearer(request)
            self.assertFalse(valid)
            self.assertEqual(error, "bearer_not_configured")


class TestVerifyHmac(unittest.TestCase):

    def test_valid_hmac(self):
        """Test valid HMAC signature."""
        secret = "hmac_secret"
        body = b'{"test": "data"}'
        expected_sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_WEBHOOK_HMAC_SECRET": secret,
                "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "0",  # Disable S36 strict default
            },
        ):
            request = MockRequest(
                headers={"X-OpenClaw-Signature": f"sha256={expected_sig}"}
            )
            valid, error = verify_hmac(request, body)
            self.assertTrue(valid)

    def test_invalid_hmac(self):
        """Test invalid HMAC signature."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_HMAC_SECRET": "secret"}):
            request = MockRequest(headers={"X-OpenClaw-Signature": "sha256=invalid"})
            valid, error = verify_hmac(request, b'{"test": "data"}')
            self.assertFalse(valid)
            self.assertEqual(error, "invalid_signature")

    def test_missing_signature(self):
        """Test missing signature header."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_HMAC_SECRET": "secret"}):
            request = MockRequest(headers={})
            valid, error = verify_hmac(request, b"test")
            self.assertFalse(valid)
            self.assertEqual(error, "missing_signature_header")

    def test_legacy_hmac_headers_are_still_accepted(self):
        secret = "hmac_secret"
        body = b'{"test": "data"}'
        expected_sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_WEBHOOK_HMAC_SECRET": secret,
                "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "0",
            },
        ):
            request = MockRequest(
                headers={"X-Moltbot-Signature": f"sha256={expected_sig}"}
            )
            with patch("services.webhook_auth.logger.warning") as warn:
                valid, error = verify_hmac(request, body)

        self.assertTrue(valid)
        self.assertEqual(error, "")
        warn.assert_called_once()

    def test_not_configured(self):
        """Test when HMAC secret is not configured."""
        with patch.dict(os.environ, {}, clear=True):
            request = MockRequest(headers={"X-OpenClaw-Signature": "sha256=abc"})
            valid, error = verify_hmac(request, b"test")
            self.assertFalse(valid)
            self.assertEqual(error, "hmac_not_configured")


class TestRequireAuth(unittest.TestCase):

    def test_bearer_mode(self):
        """Test bearer mode auth."""
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_WEBHOOK_AUTH_MODE": "bearer",
                "OPENCLAW_WEBHOOK_BEARER_TOKEN": "token123",
            },
        ):
            request = MockRequest(headers={"Authorization": "Bearer token123"})
            valid, error = require_auth(request, b"")
            self.assertTrue(valid)

    def test_hmac_mode(self):
        """Test HMAC mode auth."""
        secret = "secret"
        body = b"test"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
                "OPENCLAW_WEBHOOK_HMAC_SECRET": secret,
                "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "0",  # Disable S36 strict default
            },
        ):
            request = MockRequest(headers={"X-OpenClaw-Signature": f"sha256={sig}"})
            valid, error = require_auth(request, body)
            self.assertTrue(valid)

    def test_not_configured(self):
        """Test when auth is not configured."""
        with patch.dict(
            os.environ, {"OPENCLAW_WEBHOOK_AUTH_MODE": "bearer"}, clear=True
        ):
            request = MockRequest(headers={})
            valid, error = require_auth(request, b"")
            self.assertFalse(valid)
            self.assertEqual(error, "auth_not_configured")


class TestAuthSummary(unittest.TestCase):

    def test_summary_no_secrets(self):
        """Test that summary never contains secrets."""
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_WEBHOOK_AUTH_MODE": "bearer",
                "OPENCLAW_WEBHOOK_BEARER_TOKEN": "super_secret_token",
            },
        ):
            summary = get_auth_summary()
            self.assertEqual(summary["mode"], "bearer")
            self.assertTrue(summary["bearer_configured"])
            # Should never contain the actual token
            self.assertNotIn("super_secret_token", str(summary))

    @patch("services.idempotency_store.IdempotencyStore")
    @patch("time.time")
    def test_replay_protection_valid(self, mock_time, mock_store_cls):
        """Test valid replay protection headers."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_HMAC_SECRET": "secret"}):
            # Setup time
            mock_time.return_value = 1000.0

            # Setup store
            mock_store = MagicMock()
            mock_store.check_and_record.return_value = (False, None)
            mock_store_cls.return_value = mock_store

            request = MagicMock()
            request.headers = {
                "X-OpenClaw-Signature": "sha256=VALID_SIG",  # Will be mocked
                "X-OpenClaw-Timestamp": "1000",
                "X-OpenClaw-Nonce": "nonce123",
            }

            # We need to mock signature verification to pass first
            with patch("hmac.new") as mock_hmac:
                mock_hmac.return_value.hexdigest.return_value = "VALID_SIG"

                valid, err = verify_hmac(request, b"body")
                self.assertTrue(valid, f"Verification failed: {err}")
                self.assertEqual(err, "")

                # Verify store called
                mock_store.check_and_record.assert_called_with(
                    "nonce:nonce123", ttl=600
                )

    @patch("services.idempotency_store.IdempotencyStore")
    @patch("time.time")
    def test_replay_protection_drift(self, mock_time, mock_store_cls):
        """Test timestamp drift."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_HMAC_SECRET": "secret"}):
            mock_time.return_value = 1000.0

            request = MagicMock()
            request.headers = {
                "X-OpenClaw-Signature": "sha256=VALID_SIG",
                "X-OpenClaw-Timestamp": "500",  # Too old (diff 500 > 300)
                "X-OpenClaw-Nonce": "nonce123",
            }

            with patch("hmac.new") as mock_hmac:
                mock_hmac.return_value.hexdigest.return_value = "VALID_SIG"

                valid, err = verify_hmac(request, b"body")
                self.assertFalse(valid)
                self.assertEqual(err, "timestamp_out_of_range")

    @patch("services.idempotency_store.IdempotencyStore")
    @patch("time.time")
    def test_replay_protection_nonce_used(self, mock_time, mock_store_cls):
        """Test nonce reuse."""
        with patch.dict(os.environ, {"OPENCLAW_WEBHOOK_HMAC_SECRET": "secret"}):
            mock_time.return_value = 1000.0

            mock_store = MagicMock()
            mock_store.check_and_record.return_value = (True, "existing")  # Duplicate
            mock_store_cls.return_value = mock_store

            request = MagicMock()
            request.headers = {
                "X-OpenClaw-Signature": "sha256=VALID_SIG",
                "X-OpenClaw-Timestamp": "1000",
                "X-OpenClaw-Nonce": "nonce123",
            }

            with patch("hmac.new") as mock_hmac:
                mock_hmac.return_value.hexdigest.return_value = "VALID_SIG"

                valid, err = verify_hmac(request, b"body")
                self.assertFalse(valid)
                self.assertEqual(err, "nonce_used")

    @patch("services.idempotency_store.IdempotencyStore")
    @patch("time.time")
    def test_replay_protection_strict_enforcement(self, mock_time, mock_store_cls):
        """Test strict enforcement ensures failure when headers missing."""
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_WEBHOOK_HMAC_SECRET": "secret",
                "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "true",
            },
        ):
            mock_time.return_value = 1000.0

            # Missing headers
            request = MagicMock()
            request.headers = {
                "X-OpenClaw-Signature": "sha256=VALID_SIG",
            }
            # No Timestamp/Nonce headers provided

            with patch("hmac.new") as mock_hmac:
                mock_hmac.return_value.hexdigest.return_value = "VALID_SIG"

                valid, err = verify_hmac(request, b"body")
                self.assertFalse(valid)
                self.assertEqual(
                    err, "missing_timestamp"
                )  # Fails on first missing header


if __name__ == "__main__":
    unittest.main()
