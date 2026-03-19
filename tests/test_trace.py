"""Tests for trace utilities and trace store (R25)."""

import unittest
from unittest.mock import patch

from services.trace import TRACE_HEADER, get_effective_trace_id, normalize_trace_id
from services.trace_store import TraceStore


class TestTraceUtils(unittest.TestCase):
    def test_normalize_trace_id_accepts_safe(self):
        self.assertEqual(normalize_trace_id("abcDEF_012-"), "abcDEF_012-")

    def test_normalize_trace_id_rejects_empty(self):
        self.assertIsNone(normalize_trace_id(""))
        self.assertIsNone(normalize_trace_id("   "))

    def test_normalize_trace_id_rejects_long(self):
        self.assertIsNone(normalize_trace_id("a" * 65))

    def test_normalize_trace_id_rejects_bad_chars(self):
        self.assertIsNone(normalize_trace_id("abc\n123"))
        self.assertIsNone(normalize_trace_id("abc/123"))

    def test_get_effective_trace_id_prefers_header(self):
        headers = {TRACE_HEADER: "trace_123"}
        body = {"trace_id": "trace_999"}
        self.assertEqual(get_effective_trace_id(headers, body), "trace_123")

    def test_get_effective_trace_id_falls_back_to_body(self):
        headers = {}
        body = {"traceId": "trace_abc"}
        self.assertEqual(get_effective_trace_id(headers, body), "trace_abc")

    def test_get_effective_trace_id_generates(self):
        headers = {}
        body = {}
        tid = get_effective_trace_id(headers, body)
        self.assertTrue(isinstance(tid, str) and len(tid) >= 16)

    def test_get_effective_trace_id_accepts_legacy_header(self):
        with patch("services.trace.logger.warning") as warn:
            tid = get_effective_trace_id({"X-Moltbot-Trace-Id": "trace_legacy"}, {})

        self.assertEqual(tid, "trace_legacy")
        warn.assert_called_once()


class TestTraceStore(unittest.TestCase):
    def test_trace_store_records_events(self):
        store = TraceStore(max_size=10, ttl_sec=60)
        store.add_event("p1", "t1", "queued", {"source": "test"})
        store.add_event("p1", "t1", "completed")
        rec = store.get("p1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.trace_id, "t1")
        self.assertEqual([e.event for e in rec.events], ["queued", "completed"])


if __name__ == "__main__":
    unittest.main()
