"""
F46 Worker Endpoint E2E Tests.

Tests the end-to-end flow: BridgeClient -> BridgeHandlers worker endpoints.
Validates contract alignment, auth enforcement, and data round-trip.
"""

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_auth_request(
    method="GET",
    path="/bridge/worker/poll",
    headers=None,
    body=None,
    query=None,
    match_info=None,
):
    """Create a mock aiohttp request with bridge auth headers."""
    req = MagicMock()
    req.method = method
    req.path = path
    req.headers = {
        "X-OpenClaw-Device-Id": "worker-1",
        "X-OpenClaw-Device-Token": "bridge-auth-sample",
        "X-OpenClaw-Scopes": "job:submit,job:status",
        **(headers or {}),
    }
    req.query = query or {}
    req.match_info = match_info or {}
    if body is not None:
        req.json = AsyncMock(return_value=body)
    else:
        req.json = AsyncMock(return_value={})
    return req


def _make_noauth_request(path="/bridge/worker/poll", query=None, match_info=None):
    """Create a request with NO auth headers."""
    req = MagicMock()
    req.headers = {}
    req.query = query or {}
    req.match_info = match_info or {}
    req.json = AsyncMock(return_value={})
    return req


def _setup_bridge_env():
    """Configure environment for bridge authentication."""
    os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
    os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "bridge-auth-sample"


def _cleanup_bridge_env():
    """Clean up bridge environment."""
    for key in [
        "OPENCLAW_BRIDGE_ENABLED",
        "OPENCLAW_BRIDGE_DEVICE_TOKEN",
        "MOLTBOT_BRIDGE_ENABLED",
        "MOLTBOT_BRIDGE_DEVICE_TOKEN",
    ]:
        os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# Contract Alignment
# ---------------------------------------------------------------------------


class TestF46WorkerContractAlignment(unittest.TestCase):
    """F46: Bridge server routes match contract-defined paths."""

    def test_register_bridge_routes_includes_worker_paths(self):
        """Registration wires all 6 routes (3 server + 3 worker)."""
        from api.bridge import BridgeHandlers, register_bridge_routes

        mock_app = MagicMock()
        mock_router = MagicMock()
        mock_app.router = mock_router

        handlers = BridgeHandlers()
        register_bridge_routes(mock_app, handlers)

        # Collect all registered paths
        get_paths = [call.args[0] for call in mock_router.add_get.call_args_list]
        post_paths = [call.args[0] for call in mock_router.add_post.call_args_list]

        # Server-facing
        self.assertIn("/bridge/health", get_paths)
        self.assertIn("/bridge/submit", post_paths)
        self.assertIn("/bridge/deliver", post_paths)
        self.assertIn("/bridge/handshake", post_paths)

        # Worker-facing (F46)
        self.assertIn("/bridge/worker/poll", get_paths)
        self.assertIn("/bridge/worker/result/{job_id}", post_paths)
        self.assertIn("/bridge/worker/heartbeat", post_paths)

    def test_route_count(self):
        """Exactly 2 GET + 5 POST routes registered."""
        from api.bridge import BridgeHandlers, register_bridge_routes

        mock_app = MagicMock()
        mock_router = MagicMock()
        mock_app.router = mock_router

        register_bridge_routes(mock_app, BridgeHandlers())

        self.assertEqual(mock_router.add_get.call_count, 2)  # health + poll
        self.assertEqual(
            mock_router.add_post.call_count, 5
        )  # submit + deliver + result + heartbeat + handshake


# ---------------------------------------------------------------------------
# Worker Poll
# ---------------------------------------------------------------------------


class TestF46WorkerPoll(unittest.TestCase):
    """F46: GET /bridge/worker/poll endpoint behavior."""

    def setUp(self):
        _setup_bridge_env()
        import importlib

        import services.sidecar.auth as auth_mod

        importlib.reload(auth_mod)

    def tearDown(self):
        _cleanup_bridge_env()

    def test_poll_empty_returns_204(self):
        """Empty queue returns 204 No Content."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = _make_auth_request()

        resp = asyncio.run(handlers.worker_poll_handler(req))
        self.assertEqual(resp.status, 204)

    def test_poll_returns_jobs(self):
        """Poll returns queued jobs as JSON."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        handlers._worker_job_queue.append({"job_id": "j1", "template_id": "txt2img"})
        handlers._worker_job_queue.append({"job_id": "j2", "template_id": "txt2img"})

        req = _make_auth_request()
        resp = asyncio.run(handlers.worker_poll_handler(req))
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.body)
        self.assertEqual(len(body["jobs"]), 1)  # default batch=1
        self.assertEqual(body["jobs"][0]["job_id"], "j1")
        # j2 remains in queue
        self.assertEqual(len(handlers._worker_job_queue), 1)

    def test_poll_batch_param(self):
        """Batch parameter returns multiple jobs."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        for i in range(4):
            handlers._worker_job_queue.append({"job_id": f"j{i}"})

        req = _make_auth_request(query={"batch": "3"})
        resp = asyncio.run(handlers.worker_poll_handler(req))
        body = json.loads(resp.body)
        self.assertEqual(len(body["jobs"]), 3)
        self.assertEqual(len(handlers._worker_job_queue), 1)

    def test_poll_batch_max_5(self):
        """Batch size capped at 5."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        for i in range(10):
            handlers._worker_job_queue.append({"job_id": f"j{i}"})

        req = _make_auth_request(query={"batch": "100"})
        resp = asyncio.run(handlers.worker_poll_handler(req))
        body = json.loads(resp.body)
        self.assertEqual(len(body["jobs"]), 5)

    def test_poll_batch_invalid_returns_400(self):
        """Non-integer batch parameter returns 400."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = _make_auth_request(query={"batch": "abc"})
        resp = asyncio.run(handlers.worker_poll_handler(req))
        self.assertEqual(resp.status, 400)

    def test_poll_no_auth_returns_401(self):
        """Unauthenticated poll returns 401."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = _make_noauth_request()
        resp = asyncio.run(handlers.worker_poll_handler(req))
        self.assertIn(resp.status, (401, 403))


# ---------------------------------------------------------------------------
# Worker Result
# ---------------------------------------------------------------------------


class TestF46WorkerResult(unittest.TestCase):
    """F46: POST /bridge/worker/result/{job_id} endpoint behavior."""

    def setUp(self):
        _setup_bridge_env()
        import importlib

        import services.sidecar.auth as auth_mod

        importlib.reload(auth_mod)

    def tearDown(self):
        _cleanup_bridge_env()

    def test_result_accepted(self):
        """Valid result submission returns 201."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = _make_auth_request(
            method="POST",
            path="/bridge/worker/result/j1",
            body={"status": "completed", "outputs": {"image": "https://cdn/out.png"}},
            match_info={"job_id": "j1"},
            headers={"X-Idempotency-Key": "idem-001"},
        )
        resp = asyncio.run(handlers.worker_result_handler(req))
        self.assertEqual(resp.status, 201)
        body = json.loads(resp.body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["job_id"], "j1")

        # Verify stored
        self.assertIn("j1", handlers._worker_results)
        self.assertEqual(handlers._worker_results["j1"]["status"], "completed")

    def test_result_idempotency(self):
        """Duplicate submission returns cached response."""
        from api.bridge import BridgeHandlers

        async def _run():
            handlers = BridgeHandlers()

            # First submit
            req1 = _make_auth_request(
                body={"status": "completed", "outputs": {}},
                match_info={"job_id": "j2"},
                headers={"X-Idempotency-Key": "idem-dup"},
            )
            resp1 = await handlers.worker_result_handler(req1)
            self.assertEqual(resp1.status, 201)

            # Second submit with same key
            req2 = _make_auth_request(
                body={"status": "overwrite-attempt", "outputs": {}},
                match_info={"job_id": "j2"},
                headers={"X-Idempotency-Key": "idem-dup"},
            )
            resp2 = await handlers.worker_result_handler(req2)
            body2 = json.loads(resp2.body)
            # Should return first response's data (status=accepted, not overwrite-attempt)
            self.assertEqual(body2["status"], "accepted")

        asyncio.run(_run())

    def test_result_missing_job_id_returns_400(self):
        """Missing job_id returns 400."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = _make_auth_request(
            body={"status": "completed"},
            match_info={"job_id": ""},
        )
        resp = asyncio.run(handlers.worker_result_handler(req))
        self.assertEqual(resp.status, 400)

    def test_result_no_auth_returns_401(self):
        """Unauthenticated result returns 401."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = _make_noauth_request(match_info={"job_id": "j1"})
        resp = asyncio.run(handlers.worker_result_handler(req))
        self.assertIn(resp.status, (401, 403))


# ---------------------------------------------------------------------------
# Worker Heartbeat
# ---------------------------------------------------------------------------


class TestF46WorkerHeartbeat(unittest.TestCase):
    """F46: POST /bridge/worker/heartbeat endpoint behavior."""

    def setUp(self):
        _setup_bridge_env()
        import importlib

        import services.sidecar.auth as auth_mod

        importlib.reload(auth_mod)

    def tearDown(self):
        _cleanup_bridge_env()

    def test_heartbeat_stores_status(self):
        """Heartbeat records worker status."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = _make_auth_request(
            method="POST",
            body={"status": "idle", "details": {"gpu_util": 0.1}},
        )
        resp = asyncio.run(handlers.worker_heartbeat_handler(req))
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["ok"])

        # Verify stored
        self.assertIn("worker-1", handlers._worker_heartbeats)
        self.assertEqual(handlers._worker_heartbeats["worker-1"]["status"], "idle")

    def test_heartbeat_no_auth_returns_401(self):
        """Unauthenticated heartbeat returns 401."""
        from api.bridge import BridgeHandlers

        handlers = BridgeHandlers()
        req = _make_noauth_request()
        resp = asyncio.run(handlers.worker_heartbeat_handler(req))
        self.assertIn(resp.status, (401, 403))


# ---------------------------------------------------------------------------
# E2E Round-Trip: Client Config -> Server Handler
# ---------------------------------------------------------------------------


class TestF46E2ERoundTrip(unittest.TestCase):
    """F46: Verify client endpoint resolver matches server routes."""

    def test_client_paths_match_server_routes(self):
        """BridgeClient._endpoint paths match registered server routes."""
        from api.bridge import BridgeHandlers, register_bridge_routes
        from services.sidecar.bridge_client import BridgeClient

        # Collect server paths
        mock_app = MagicMock()
        mock_router = MagicMock()
        mock_app.router = mock_router
        register_bridge_routes(mock_app, BridgeHandlers())

        get_paths = {call.args[0] for call in mock_router.add_get.call_args_list}
        post_paths = {call.args[0] for call in mock_router.add_post.call_args_list}
        all_server_paths = get_paths | post_paths

        # Client paths (relative)
        client = BridgeClient("http://localhost:8188", "t", "w")
        client_poll = client._endpoint("worker_poll").replace(
            "http://localhost:8188", ""
        )
        client_hb = client._endpoint("worker_heartbeat").replace(
            "http://localhost:8188", ""
        )
        client_health = client._endpoint("health").replace("http://localhost:8188", "")

        # Direct match for parameterless endpoints
        self.assertIn(client_poll, all_server_paths)
        self.assertIn(client_hb, all_server_paths)
        self.assertIn(client_health, all_server_paths)

        # worker_result is /bridge/worker/result + /{job_id} at server
        client_result_base = client._endpoint("worker_result").replace(
            "http://localhost:8188", ""
        )
        self.assertIn(client_result_base + "/{job_id}", all_server_paths)

    def test_handlers_have_worker_state(self):
        """BridgeHandlers initializes worker state."""
        from api.bridge import BridgeHandlers

        h = BridgeHandlers()
        self.assertIsInstance(h._worker_job_queue, list)
        self.assertIsInstance(h._worker_results, dict)
        self.assertIsInstance(h._worker_heartbeats, dict)


if __name__ == "__main__":
    unittest.main()
