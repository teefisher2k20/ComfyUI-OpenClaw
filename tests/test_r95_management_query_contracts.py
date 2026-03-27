"""
R95 management query pagination and bounded-scan contract tests.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import api.approvals
import api.events
from services.management_query import (
    bounded_scan_collect,
    normalize_cursor_limit,
    normalize_limit_offset,
)


class _DummyApproval:
    def __init__(self, approval_id: str):
        self.approval_id = approval_id

    def to_dict(self):
        return {"approval_id": self.approval_id}


class _BadApprovalNoSerializer:
    pass


class _ExplodingApproval:
    def to_dict(self):
        raise RuntimeError("backend explode")


class _DummyEvent:
    def __init__(self, seq: int):
        self.seq = seq

    def to_dict(self):
        return {"seq": self.seq}


class TestR95PaginationHelpers(unittest.TestCase):
    def test_normalize_limit_offset_clamps_and_warns(self):
        page = normalize_limit_offset(
            {"limit": "9999", "offset": "-10"},
            default_limit=100,
            max_limit=500,
            max_offset=5000,
        )
        self.assertEqual(page.limit, 500)
        self.assertEqual(page.offset, 0)
        codes = {w["code"] for w in page.warnings}
        self.assertIn("R95_LIMIT_CLAMPED", codes)
        self.assertIn("R95_OFFSET_BELOW_MIN", codes)

    def test_normalize_cursor_limit_invalid_cursor_defaults(self):
        page = normalize_cursor_limit(
            {"since": "bad", "limit": "0"},
            cursor_key="since",
            default_cursor=0,
            min_cursor=0,
            default_limit=50,
            max_limit=200,
        )
        self.assertEqual(page.cursor, 0)
        self.assertEqual(page.limit, 1)
        codes = {w["code"] for w in page.warnings}
        self.assertIn("R95_INVALID_CURSOR", codes)
        self.assertIn("R95_LIMIT_BELOW_MIN", codes)

    def test_bounded_scan_collect_skips_malformed_and_not_swallow_runtime_errors(self):
        result = bounded_scan_collect(
            [_DummyApproval("a"), _BadApprovalNoSerializer(), _DummyApproval("b")],
            skip=0,
            take=10,
            scan_cap=10,
            serializer=lambda x: x.to_dict(),
        )
        self.assertEqual([i["approval_id"] for i in result.items], ["a", "b"])
        self.assertEqual(result.skipped_malformed, 1)

        with self.assertRaises(RuntimeError):
            bounded_scan_collect(
                [_ExplodingApproval()],
                skip=0,
                take=1,
                scan_cap=10,
                serializer=lambda x: x.to_dict(),
            )


class TestR95EventsApi(unittest.IsolatedAsyncioTestCase):
    async def test_events_poll_normalizes_and_resets_stale_cursor(self):
        req = MagicMock()
        req.query = {"since": "3", "limit": "2"}
        req.headers = {}

        class StubStore:
            def __init__(self):
                self.calls = []

            def latest_seq(self):
                return 100

            def events_since_bounded(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return [], {
                        "scanned": 5,
                        "scan_cap": kwargs["scan_cap"],
                        "truncated": False,
                        "earliest_retained_seq": 50,
                        "latest_retained_seq": 100,
                    }
                return [_DummyEvent(50), _DummyEvent(51)], {
                    "scanned": 2,
                    "scan_cap": kwargs["scan_cap"],
                    "truncated": False,
                    "earliest_retained_seq": 50,
                    "latest_retained_seq": 100,
                }

        store = StubStore()
        fake_web = SimpleNamespace(
            json_response=MagicMock(return_value=SimpleNamespace(status=200))
        )

        with (
            patch.object(api.events, "web", fake_web),
            patch.object(api.events, "check_rate_limit", return_value=True),
            patch.object(
                api.events, "require_observability_access", return_value=(True, None)
            ),
            patch.object(api.events, "get_job_event_store", return_value=store),
        ):
            resp = await api.events.events_poll_handler(req)
            self.assertEqual(resp.status, 200)

        payload = fake_web.json_response.call_args.args[0]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pagination"]["cursor_status"], "stale_cursor_reset")
        self.assertEqual(payload["pagination"]["since_requested"], 3)
        self.assertEqual(payload["pagination"]["since_effective"], 49)
        self.assertEqual(payload["delta"]["requested_since_seq"], 3)
        self.assertEqual(payload["delta"]["effective_since_seq"], 49)
        self.assertEqual(payload["delta"]["next_since_seq"], 51)
        self.assertTrue(payload["delta"]["truncated"])
        self.assertEqual([e["seq"] for e in payload["events"]], [50, 51])
        self.assertEqual(len(store.calls), 2)

    async def test_events_poll_future_cursor_and_invalid_limit(self):
        req = MagicMock()
        req.query = {"since": "999", "limit": "bad"}
        req.headers = {}

        class StubStore:
            def latest_seq(self):
                return 10

            def events_since_bounded(self, **kwargs):
                return [], {
                    "scanned": 0,
                    "scan_cap": kwargs["scan_cap"],
                    "truncated": False,
                    "earliest_retained_seq": None,
                    "latest_retained_seq": 10,
                }

        fake_web = SimpleNamespace(
            json_response=MagicMock(return_value=SimpleNamespace(status=200))
        )
        with (
            patch.object(api.events, "web", fake_web),
            patch.object(api.events, "check_rate_limit", return_value=True),
            patch.object(
                api.events, "require_observability_access", return_value=(True, None)
            ),
            patch.object(api.events, "get_job_event_store", return_value=StubStore()),
        ):
            await api.events.events_poll_handler(req)

        payload = fake_web.json_response.call_args.args[0]
        self.assertEqual(payload["pagination"]["cursor_status"], "future_cursor_reset")
        self.assertEqual(payload["pagination"]["since_effective"], 10)
        self.assertEqual(payload["delta"]["next_since_seq"], 10)
        self.assertFalse(payload["delta"]["truncated"])
        codes = {w["code"] for w in payload["pagination"]["warnings"]}
        self.assertIn("R95_INVALID_LIMIT", codes)
        self.assertIn("R95_STALE_CURSOR_FUTURE", codes)

    async def test_events_poll_does_not_swallow_backend_errors(self):
        req = MagicMock()
        req.query = {}
        req.headers = {}

        class StubStore:
            def latest_seq(self):
                return 1

            def events_since_bounded(self, **kwargs):
                raise RuntimeError("store failure")

        with (
            patch.object(api.events, "check_rate_limit", return_value=True),
            patch.object(
                api.events, "require_observability_access", return_value=(True, None)
            ),
            patch.object(api.events, "get_job_event_store", return_value=StubStore()),
        ):
            with self.assertRaises(RuntimeError):
                await api.events.events_poll_handler(req)


class TestR95ApprovalsApi(unittest.IsolatedAsyncioTestCase):
    async def test_approvals_list_normalizes_pagination_and_skips_malformed(self):
        req = MagicMock()
        req.query = {"limit": "9999", "offset": "-7"}

        handler = api.approvals.ApprovalHandlers(
            require_admin_token_fn=lambda _r: (True, None)
        )
        handler._service = MagicMock()
        handler._service.list_all.return_value = [
            _DummyApproval("a1"),
            _BadApprovalNoSerializer(),
            _DummyApproval("a2"),
        ]
        handler._service.count_pending.return_value = 1

        fake_web = SimpleNamespace(
            json_response=MagicMock(return_value=SimpleNamespace(status=200))
        )
        with patch.object(api.approvals, "web", fake_web):
            resp = await handler.list_approvals(req)
            self.assertEqual(resp.status, 200)

        payload = fake_web.json_response.call_args.args[0]
        self.assertEqual(payload["count"], 2)
        self.assertEqual([a["approval_id"] for a in payload["approvals"]], ["a1", "a2"])
        self.assertEqual(payload["pagination"]["limit"], 500)
        self.assertEqual(payload["pagination"]["offset"], 0)
        self.assertEqual(payload["scan"]["skipped_malformed"], 1)
        warn_codes = {w["code"] for w in payload["pagination"]["warnings"]}
        self.assertIn("R95_LIMIT_CLAMPED", warn_codes)
        self.assertIn("R95_OFFSET_BELOW_MIN", warn_codes)

    async def test_approvals_list_invalid_status_still_400(self):
        req = MagicMock()
        req.query = {"status": "not-a-status"}

        handler = api.approvals.ApprovalHandlers(
            require_admin_token_fn=lambda _r: (True, None)
        )
        fake_web = SimpleNamespace(
            json_response=MagicMock(return_value=SimpleNamespace(status=400))
        )
        with patch.object(api.approvals, "web", fake_web):
            resp = await handler.list_approvals(req)
            self.assertEqual(resp.status, 400)

    async def test_approvals_list_does_not_swallow_backend_errors(self):
        req = MagicMock()
        req.query = {}

        handler = api.approvals.ApprovalHandlers(
            require_admin_token_fn=lambda _r: (True, None)
        )
        handler._service = MagicMock()
        handler._service.list_all.side_effect = RuntimeError("db failed")

        with self.assertRaises(RuntimeError):
            await handler.list_approvals(req)


if __name__ == "__main__":
    unittest.main()
