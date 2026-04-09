"""
R123: Real-backend model-list lane (low-mock).

This suite validates /openclaw/llm/models through a real aiohttp upstream
service and real safe_io SSRF checks. It closes the loopback-regression gap
left by mock-heavy settings/model-list tests.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from api.config import _MODEL_LIST_CACHE, llm_models_handler


class TestR123RealBackendModelListLane(AioHTTPTestCase):
    """Low-mock backend lane for llm model-list SSRF parity."""

    def setUp(self):
        super().setUp()
        self._patchers = []
        self._fixtures_dir = tempfile.mkdtemp(prefix="openclaw-r123-")
        self._upstream_server = None
        self._upstream_thread = None
        self._upstream_base_url = ""
        self._models_payload = {
            "data": [{"id": "gemma3:4b"}, {"id": "llama3.2:3b"}],
        }

        env_patch = patch.dict(
            os.environ,
            {
                "OPENCLAW_ADMIN_TOKEN": "r123-admin-token",
                # IMPORTANT: real-backend lane focuses on model-list SSRF parity,
                # so keep remote-admin guard out of scope for deterministic results.
                "OPENCLAW_ALLOW_REMOTE_ADMIN": "1",
                "OPENCLAW_DEPLOYMENT_PROFILE": "local",
                "MOLTBOT_STATE_DIR": os.path.join(self._fixtures_dir, "state"),
            },
        )
        env_patch.start()
        self._patchers.append(env_patch)
        _MODEL_LIST_CACHE.clear()
        self._start_upstream_http_server()

    def _start_upstream_http_server(self):
        payload_bytes = json.dumps(self._models_payload).encode("utf-8")

        class ModelsHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/v1/models":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload_bytes)))
                    self.end_headers()
                    self.wfile.write(payload_bytes)
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                return

        bind_ports = (11434, 1234, 5000, 8080, 8443)
        last_err = None
        for port in bind_ports:
            try:
                # CRITICAL:
                # Debug finding from R123 bring-up:
                # - llm_models_handler -> safe_request_json uses synchronous urllib.
                # - If upstream is served by aiohttp TestServer on the same event loop,
                #   request handling can deadlock/time out (handler waits on itself).
                # Keep upstream in a separate threaded HTTP server to preserve real HTTP
                # behavior while avoiding same-loop re-entrancy deadlock.
                server = ThreadingHTTPServer(("127.0.0.1", port), ModelsHandler)
                self._upstream_server = server
                self._upstream_base_url = f"http://127.0.0.1:{port}"
                self._upstream_thread = threading.Thread(
                    target=server.serve_forever,
                    kwargs={"poll_interval": 0.05},
                    daemon=True,
                )
                self._upstream_thread.start()
                return
            except OSError as e:
                last_err = e

        raise RuntimeError(
            f"Unable to bind upstream HTTP server on allowed ports {bind_ports}: {last_err}"
        )

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        if self._upstream_server is not None:
            self._upstream_server.shutdown()
            self._upstream_server.server_close()
            self._upstream_server = None
        self._upstream_thread = None
        _MODEL_LIST_CACHE.clear()
        shutil.rmtree(self._fixtures_dir, ignore_errors=True)
        super().tearDown()

    async def get_application(self):
        app = web.Application()
        app.router.add_get("/openclaw/llm/models", llm_models_handler)
        return app

    def _admin_headers(self):
        return {"X-OpenClaw-Admin-Token": "r123-admin-token"}

    @unittest_run_loop
    async def test_local_ollama_loopback_models_success_openclaw(self):
        base_url = self._upstream_base_url
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_LLM_PROVIDER": "ollama",
                "OPENCLAW_LLM_BASE_URL": base_url,
            },
        ):
            _MODEL_LIST_CACHE.clear()
            resp = await self.client.get(
                "/openclaw/llm/models",
                headers=self._admin_headers(),
            )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("gemma3:4b", data["models"])

    @unittest_run_loop
    async def test_local_lmstudio_loopback_models_success_legacy_prefix(self):
        base_url = f"{self._upstream_base_url}/v1"
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_LLM_PROVIDER": "lmstudio",
                "OPENCLAW_LLM_BASE_URL": base_url,
            },
        ):
            _MODEL_LIST_CACHE.clear()
            resp = await self.client.get(
                "/openclaw/llm/models",
                headers=self._admin_headers(),
            )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["provider"], "lmstudio")
        self.assertIn("llama3.2:3b", data["models"])

    @unittest_run_loop
    async def test_private_non_loopback_ip_is_fail_closed(self):
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_LLM_PROVIDER": "ollama",
                "OPENCLAW_LLM_BASE_URL": "http://192.168.1.5:11434",
                "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "1",
            },
        ):
            _MODEL_LIST_CACHE.clear()
            resp = await self.client.get(
                "/openclaw/llm/models",
                headers=self._admin_headers(),
            )

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertIn("SSRF policy blocked", data.get("error", ""))
        self.assertIn("Private/reserved IP blocked", data.get("error", ""))

    @unittest_run_loop
    async def test_allow_any_public_host_does_not_break_loopback_parity(self):
        base_url = self._upstream_base_url
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_LLM_PROVIDER": "ollama",
                "OPENCLAW_LLM_BASE_URL": base_url,
                "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "1",
            },
        ):
            _MODEL_LIST_CACHE.clear()
            resp = await self.client.get(
                "/openclaw/llm/models",
                headers=self._admin_headers(),
            )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("gemma3:4b", data["models"])


if __name__ == "__main__":
    unittest.main()
