import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from aiohttp import web
    from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
except Exception:  # pragma: no cover
    web = None  # type: ignore
    AioHTTPTestCase = unittest.TestCase  # type: ignore

    def unittest_run_loop(fn):  # type: ignore
        return fn

from api import model_manager as mm_api
from services.model_manager import ModelManager


@unittest.skipIf(web is None, "aiohttp not installed")
class TestModelManagerAPI(AioHTTPTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_model_manager_api_")
        self.manager = ModelManager(
            state_root=Path(self.tmp.name) / "state",
            install_root=Path(self.tmp.name) / "install",
        )
        self.manager.allow_any_public = True
        self._orig_manager = mm_api.model_manager
        mm_api.model_manager = self.manager

    def tearDown(self):
        mm_api.model_manager = self._orig_manager
        self.tmp.cleanup()
        super().tearDown()

    async def get_application(self):
        app = web.Application()
        app.router.add_get("/openclaw/models/search", mm_api.model_search_handler)
        app.router.add_post("/openclaw/models/downloads", mm_api.model_download_create_handler)
        app.router.add_get("/openclaw/models/downloads", mm_api.model_download_list_handler)
        app.router.add_get("/openclaw/models/downloads/{task_id}", mm_api.model_download_get_handler)
        app.router.add_post("/openclaw/models/downloads/{task_id}/cancel", mm_api.model_download_cancel_handler)
        app.router.add_post("/openclaw/models/import", mm_api.model_import_handler)
        app.router.add_get("/openclaw/models/installations", mm_api.model_installations_list_handler)
        return app

    async def _wait_task_terminal(self, task_id: str, timeout: float = 3.0):
        end = time.time() + timeout
        while time.time() < end:
            resp = await self.client.get(f"/openclaw/models/downloads/{task_id}")
            body = await resp.json()
            if body["task"]["state"] in {"completed", "failed", "cancelled"}:
                return body["task"]
            time.sleep(0.02)
        self.fail(f"Task {task_id} did not finish")

    @patch("api.model_manager.require_admin_token", return_value=(True, None))
    @patch("services.model_manager.validate_outbound_url", return_value=("https", "example.com", 443, ["1.1.1.1"]))
    @unittest_run_loop
    async def test_download_and_import_contract(self, _mock_validate, _mock_admin):
        payload = b"api-model-bytes"
        digest = hashlib.sha256(payload).hexdigest()

        def fake_download(task, _cancel_event):
            stage = self.manager.staging_dir / task.task_id
            stage.mkdir(parents=True, exist_ok=True)
            final = stage / task.filename
            final.write_bytes(payload)
            return str(final), digest

        self.manager._download = fake_download  # type: ignore[assignment]

        create_resp = await self.client.post(
            "/openclaw/models/downloads",
            json={
                "model_id": "api-model",
                "name": "API Model",
                "model_type": "checkpoint",
                "source": "catalog",
                "source_label": "Catalog",
                "download_url": "https://example.com/api-model.safetensors",
                "expected_sha256": digest,
                "provenance": {
                    "publisher": "OpenClaw",
                    "license": "OpenRAIL",
                    "source_url": "https://example.com/api-model",
                },
            },
        )
        self.assertEqual(create_resp.status, 201)
        created = await create_resp.json()
        task_id = created["task"]["task_id"]

        done = await self._wait_task_terminal(task_id)
        self.assertEqual(done["state"], "completed")

        import_resp = await self.client.post("/openclaw/models/import", json={"task_id": task_id})
        self.assertEqual(import_resp.status, 200)
        imported = await import_resp.json()
        self.assertTrue(imported["ok"])
        self.assertEqual(imported["installation"]["model_id"], "api-model")

        search_resp = await self.client.get("/openclaw/models/search?installed=true")
        self.assertEqual(search_resp.status, 200)
        search_body = await search_resp.json()
        self.assertTrue(search_body["ok"])
        self.assertEqual(search_body["pagination"]["total"], 1)
        self.assertEqual(search_body["items"][0]["id"], "api-model")

        installs_resp = await self.client.get("/openclaw/models/installations")
        installs_body = await installs_resp.json()
        self.assertEqual(installs_resp.status, 200)
        self.assertEqual(installs_body["pagination"]["total"], 1)

    @patch("api.model_manager.require_admin_token", return_value=(False, "invalid_admin_token"))
    @unittest_run_loop
    async def test_admin_gating(self, _mock_admin):
        resp = await self.client.get("/openclaw/models/search")
        self.assertEqual(resp.status, 403)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
