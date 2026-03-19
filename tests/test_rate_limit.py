"""
Tests for Rate Limiting Service (S17).
"""

import os
import time
import unittest
from unittest.mock import patch

from services.rate_limit import (
    RateLimitDecision,
    RateLimiter,
    TokenBucket,
    build_rate_limit_payload,
)


class TestRateLimit(unittest.TestCase):

    def test_token_bucket(self):
        # 2 tokens capacity, refill 10 per second
        bucket = TokenBucket(2, 10.0)

        # Consume 2 immediately
        self.assertTrue(bucket.consume(1))
        self.assertTrue(bucket.consume(1))

        # Should be empty
        self.assertFalse(bucket.consume(1))

        # Wait 0.15s (should refill ~1.5 tokens -> cap at 2 if full wait, but here ~1.5)
        # 10 tokens/sec * 0.15 = 1.5 tokens
        time.sleep(0.15)
        self.assertTrue(bucket.consume(1))

    def test_rate_limiter_defaults(self):
        limiter = RateLimiter()

        # Webhook: 30/min
        ip = "1.2.3.4"

        # Should be able to consume 30
        for _ in range(30):
            self.assertTrue(limiter.check("webhook", ip))

        # 31st should fail (assuming this runs fast enough < 2s generally)
        self.assertFalse(limiter.check("webhook", ip))

    def test_rate_limiter_separation(self):
        limiter = RateLimiter()
        ip = "10.0.0.5"

        # Exhaust webhook bucket
        for _ in range(30):
            limiter.check("webhook", ip)
        self.assertFalse(limiter.check("webhook", ip))

        # Logs bucket should still be fresh (60 capacity)
        self.assertTrue(limiter.check("logs", ip))

    def test_rate_limiter_ip_separation(self):
        limiter = RateLimiter()

        # Exhaust IP A
        for _ in range(30):
            limiter.check("webhook", "1.1.1.1")
        self.assertFalse(limiter.check("webhook", "1.1.1.1"))

        # IP B should be fresh
        self.assertTrue(limiter.check("webhook", "2.2.2.2"))

    def test_rate_limiter_token_scope_isolates_shared_ip(self):
        limiter = RateLimiter()
        ip = "10.10.10.10"

        for _ in range(20):
            self.assertTrue(limiter.check("admin", ip, token_id="kid-a"))

        self.assertFalse(limiter.check("admin", ip, token_id="kid-a"))
        self.assertTrue(limiter.check("admin", ip, token_id="kid-b"))

    def test_rate_limiter_tenant_scope_isolates_shared_ip(self):
        limiter = RateLimiter()
        ip = "10.10.10.20"

        for _ in range(60):
            self.assertTrue(limiter.evaluate("admin", ip, tenant_id="tenant-a").allowed)

        self.assertFalse(limiter.evaluate("admin", ip, tenant_id="tenant-a").allowed)
        self.assertTrue(limiter.evaluate("admin", ip, tenant_id="tenant-b").allowed)

    def test_rate_limiter_daily_cap_returns_daily_reason(self):
        limiter = RateLimiter()
        with patch.dict(os.environ, {"OPENCLAW_RATE_LIMIT_ADMIN_DAILY_CAP": "2"}):
            first = limiter.evaluate("admin", "9.9.9.9", token_id="kid-daily")
            second = limiter.evaluate("admin", "9.9.9.9", token_id="kid-daily")
            denied = limiter.evaluate("admin", "9.9.9.9", token_id="kid-daily")

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.bucket, "daily")
        self.assertEqual(denied.reason_code, "daily_cap_exceeded")
        self.assertGreaterEqual(denied.retry_after_sec, 1)

    def test_build_rate_limit_payload_includes_machine_readable_fields(self):
        request = type("FakeRequest", (), {})()
        setattr(
            request,
            "_openclaw_rate_limit_decisions",
            {
                "admin": RateLimitDecision(
                    allowed=False,
                    limit_type="admin",
                    bucket="token_id",
                    scope="token_id:kid-abc",
                    retry_after_sec=17,
                    reason_code="burst_limit_exceeded",
                    endpoint_class="admin",
                    ip="127.0.0.1",
                    token_id="kid-abc",
                    tenant_id="default",
                )
            },
        )

        payload = build_rate_limit_payload(
            request,
            "admin",
            error="Rate limit exceeded",
            include_ok=True,
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "rate_limit_exceeded")
        self.assertEqual(payload["bucket"], "token_id")
        self.assertEqual(payload["scope"], "token_id:kid-abc")
        self.assertEqual(payload["retry_after_sec"], 17)
        self.assertEqual(payload["reason_code"], "burst_limit_exceeded")


if __name__ == "__main__":
    unittest.main()
