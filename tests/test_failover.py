"""
Unit tests for R14 Failover Routing.
"""

import os
import tempfile
import time
import unittest

from services.failover import (
    CooldownEntry,
    ErrorCategory,
    FailoverState,
    classify_cooldown,
    classify_error,
    get_cooldown_duration,
    get_failover_candidates,
    should_failover,
    should_retry,
)


class TestErrorClassification(unittest.TestCase):
    """Test error classification logic."""

    def test_auth_errors(self):
        """Should classify auth errors correctly."""
        self.assertEqual(
            classify_error(Exception("Unauthorized"), 401)[0], ErrorCategory.AUTH
        )
        self.assertEqual(
            classify_error(Exception("Forbidden"), 403)[0], ErrorCategory.AUTH
        )
        self.assertEqual(
            classify_error(Exception("unauthorized access"))[0], ErrorCategory.AUTH
        )

    def test_billing_errors(self):
        """Should classify billing/quota errors."""
        self.assertEqual(
            classify_error(Exception("Quota exceeded"), 402)[0], ErrorCategory.BILLING
        )
        self.assertEqual(
            classify_error(Exception("Insufficient quota"), 429)[0],
            ErrorCategory.BILLING,
        )

    def test_rate_limit_errors(self):
        """Should classify rate limit errors."""
        self.assertEqual(
            classify_error(Exception("Too many requests"), 429)[0],
            ErrorCategory.RATE_LIMIT,
        )
        self.assertEqual(
            classify_error(Exception("Rate limit exceeded"))[0],
            ErrorCategory.RATE_LIMIT,
        )

    def test_retry_after_reason_code_is_preserved(self):
        """429 + retry-after should surface explicit cooldown diagnostics."""

        class RetryAfterError(Exception):
            retry_after = 42

        decision = classify_cooldown(RetryAfterError("Too many requests"), 429)
        self.assertEqual(decision.category, ErrorCategory.RATE_LIMIT)
        self.assertEqual(decision.reason_code, "provider_retry_after")
        self.assertEqual(decision.bucket, "provider_cooldown")
        self.assertEqual(decision.retry_after_sec, 42)

    def test_quota_reason_code_is_preserved(self):
        """Quota/billing style errors should map to provider_quota diagnostics."""
        decision = classify_cooldown(Exception("Insufficient quota"), 429)
        self.assertEqual(decision.category, ErrorCategory.BILLING)
        self.assertEqual(decision.reason_code, "provider_quota_exceeded")
        self.assertEqual(decision.bucket, "provider_quota")

    def test_timeout_errors(self):
        """Should classify timeout errors."""
        self.assertEqual(
            classify_error(Exception("Request timed out"))[0], ErrorCategory.TIMEOUT
        )
        self.assertEqual(
            classify_error(Exception("Connection timeout"))[0], ErrorCategory.TIMEOUT
        )

    def test_invalid_request_errors(self):
        """Should classify invalid request errors."""
        self.assertEqual(
            classify_error(Exception("Bad request"), 400)[0],
            ErrorCategory.INVALID_REQUEST,
        )
        self.assertEqual(
            classify_error(Exception("Validation error"), 422)[0],
            ErrorCategory.INVALID_REQUEST,
        )

    def test_unknown_errors(self):
        """Should default to unknown for unclassified errors."""
        self.assertEqual(
            classify_error(Exception("Some random error"))[0], ErrorCategory.UNKNOWN
        )


class TestRetryDecisions(unittest.TestCase):
    """Test retry vs failover decision logic."""

    def test_should_retry_transient(self):
        """Should retry transient errors."""
        self.assertTrue(should_retry(ErrorCategory.TIMEOUT))
        self.assertTrue(should_retry(ErrorCategory.RATE_LIMIT))

    def test_should_not_retry_persistent(self):
        """Should not retry persistent errors."""
        self.assertFalse(should_retry(ErrorCategory.AUTH))
        self.assertFalse(should_retry(ErrorCategory.BILLING))
        self.assertFalse(should_retry(ErrorCategory.INVALID_REQUEST))

    def test_should_failover_persistent(self):
        """Should failover for persistent errors."""
        self.assertTrue(should_failover(ErrorCategory.AUTH))
        self.assertTrue(should_failover(ErrorCategory.BILLING))
        self.assertTrue(should_failover(ErrorCategory.INVALID_REQUEST))
        self.assertTrue(should_failover(ErrorCategory.UNKNOWN))


class TestCooldownManagement(unittest.TestCase):
    """Test cooldown state management."""

    def setUp(self):
        """Create temporary state file."""
        self.temp_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.temp_file.close()
        self.state_file = self.temp_file.name

    def tearDown(self):
        """Clean up temporary file."""
        if os.path.exists(self.state_file):
            os.unlink(self.state_file)

    def test_cooldown_entry_is_active(self):
        """Should correctly check if cooldown is active."""
        # Active cooldown
        entry = CooldownEntry(
            provider="openai",
            model="gpt-4",
            reason="rate_limit",
            until=time.time() + 60,
        )
        self.assertTrue(entry.is_active())

        # Expired cooldown
        entry_expired = CooldownEntry(
            provider="openai",
            model="gpt-4",
            reason="rate_limit",
            until=time.time() - 60,
        )
        self.assertFalse(entry_expired.is_active())

    def test_set_and_check_cooldown(self):
        """Should set and check cooldowns correctly."""
        state = FailoverState(self.state_file)

        # Set cooldown
        state.set_cooldown(
            "openai",
            "gpt-4",
            "rate_limit",
            60,
            reason_code="provider_rate_limited",
            bucket="provider_cooldown",
        )

        # Should be in cooldown
        self.assertTrue(state.is_cooling_down("openai", "gpt-4"))

        # Different provider/model should not be in cooldown
        self.assertFalse(state.is_cooling_down("anthropic", "claude-3"))

    def test_cooldown_persistence(self):
        """Should persist cooldown state to disk."""
        state1 = FailoverState(self.state_file)
        state1.set_cooldown(
            "openai",
            "gpt-4",
            "auth",
            3600,
            reason_code="provider_auth_failed",
            bucket="provider_auth",
        )

        # Create new instance (simulates restart)
        state2 = FailoverState(self.state_file)

        # Should load persisted cooldown
        self.assertTrue(state2.is_cooling_down("openai", "gpt-4"))

    def test_cooldown_expiration(self):
        """Should expire cooldowns after duration."""
        state = FailoverState(self.state_file)

        # Set very short cooldown
        state.set_cooldown("openai", "gpt-4", "test", 0.1)

        # Initially in cooldown
        self.assertTrue(state.is_cooling_down("openai", "gpt-4"))

        # Wait for expiration
        time.sleep(0.2)

        # Should be expired
        self.assertFalse(state.is_cooling_down("openai", "gpt-4"))

    def test_clear_cooldown(self):
        """Should clear cooldowns manually."""
        state = FailoverState(self.state_file)
        state.set_cooldown("openai", "gpt-4", "test", 3600)

        self.assertTrue(state.is_cooling_down("openai", "gpt-4"))

        state.clear_cooldown("openai", "gpt-4")

        self.assertFalse(state.is_cooling_down("openai", "gpt-4"))

    def test_no_secrets_in_state(self):
        """Should not persist secrets in state file."""
        state = FailoverState(self.state_file)
        state.set_cooldown(
            "openai",
            "gpt-4",
            "auth_failed",
            60,
            reason_code="provider_auth_failed",
            bucket="provider_auth",
        )

        # Read raw file
        with open(self.state_file, "r") as f:
            content = f.read()

        # Should not contain common secret patterns
        self.assertNotIn("sk-", content)
        self.assertNotIn("api_key", content)
        self.assertNotIn("password", content)


class TestFailoverCandidates(unittest.TestCase):
    """Test failover candidate generation."""

    def test_primary_only(self):
        """Should return primary when no fallbacks."""
        candidates = get_failover_candidates("openai", "gpt-4")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0], ("openai", "gpt-4"))

    def test_model_fallbacks(self):
        """Should include model fallbacks."""
        candidates = get_failover_candidates(
            "openai", "gpt-4", fallback_models=["gpt-3.5-turbo", "gpt-4-turbo"]
        )
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0], ("openai", "gpt-4"))
        self.assertEqual(candidates[1], ("openai", "gpt-3.5-turbo"))
        self.assertEqual(candidates[2], ("openai", "gpt-4-turbo"))

    def test_provider_fallbacks(self):
        """Should include provider fallbacks."""
        candidates = get_failover_candidates(
            "openai", "gpt-4", fallback_providers=["anthropic", "groq"]
        )
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0], ("openai", "gpt-4"))
        self.assertEqual(candidates[1], ("anthropic", "gpt-4"))
        self.assertEqual(candidates[2], ("groq", "gpt-4"))

    def test_combined_fallbacks(self):
        """Should include both model and provider fallbacks."""
        candidates = get_failover_candidates(
            "openai",
            "gpt-4",
            fallback_models=["gpt-3.5-turbo"],
            fallback_providers=["anthropic"],
        )
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0], ("openai", "gpt-4"))
        self.assertEqual(candidates[1], ("openai", "gpt-3.5-turbo"))
        self.assertEqual(candidates[2], ("anthropic", "gpt-4"))


class TestCooldownDurations(unittest.TestCase):
    """Test cooldown duration logic."""

    def test_auth_cooldown_longest(self):
        """Auth errors should have longest cooldown."""
        auth_duration = get_cooldown_duration(ErrorCategory.AUTH)
        rate_limit_duration = get_cooldown_duration(ErrorCategory.RATE_LIMIT)
        self.assertGreater(auth_duration, rate_limit_duration)

    def test_all_categories_have_durations(self):
        """All error categories should have defined durations."""
        for category in ErrorCategory:
            duration = get_cooldown_duration(category)
            self.assertGreater(duration, 0)


if __name__ == "__main__":
    unittest.main()
