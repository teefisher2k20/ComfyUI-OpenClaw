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
from services.request_contracts import R144_IO_BOUNDARY_MATRIX


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
        app.router.add_post(
            "/openclaw/models/downloads", mm_api.model_download_create_handler
        )
        app.router.add_get(
            "/openclaw/models/downloads", mm_api.model_download_list_handler
        )
        app.router.add_get(
            "/openclaw/models/downloads/{task_id}", mm_api.model_download_get_handler
        )
        app.router.add_post(
            "/openclaw/models/downloads/{task_id}/cancel",
            mm_api.model_download_cancel_handler,
        )
        app.router.add_post("/openclaw/models/import", mm_api.model_import_handler)
        app.router.add_get(
            "/openclaw/models/installations", mm_api.model_installations_list_handler
        )
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

    async def _create_completed_task(self, *, model_id: str = "api-model") -> str:
        payload = f"{model_id}-bytes".encode("utf-8")
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
                "model_id": model_id,
                "name": "API Model",
                "model_type": "checkpoint",
                "source": "catalog",
                "source_label": "Catalog",
                "download_url": f"https://example.com/{model_id}.safetensors",
                "expected_sha256": digest,
                "provenance": {
                    "publisher": "OpenClaw",
                    "license": "OpenRAIL",
                    "source_url": f"https://example.com/{model_id}",
                },
            },
        )
        self.assertEqual(create_resp.status, 201)
        created = await create_resp.json()
        task_id = created["task"]["task_id"]
        done = await self._wait_task_terminal(task_id)
        self.assertEqual(done["state"], "completed")
        return task_id

    @patch("api.model_manager.require_admin_token", return_value=(True, None))
    @patch(
        "services.model_manager.validate_outbound_url",
        return_value=("https", "example.com", 443, ["1.1.1.1"]),
    )
    @unittest_run_loop
    async def test_download_and_import_contract(self, _mock_validate, _mock_admin):
        task_id = await self._create_completed_task(model_id="api-model")

        import_resp = await self.client.post(
            "/openclaw/models/import", json={"task_id": task_id}
        )
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

    @patch(
        "api.model_manager.require_admin_token",
        return_value=(False, "invalid_admin_token"),
    )
    @unittest_run_loop
    async def test_admin_gating(self, _mock_admin):
        resp = await self.client.get("/openclaw/models/search")
        self.assertEqual(resp.status, 403)

    @patch("api.model_manager.require_admin_token", return_value=(True, None))
    @patch(
        "services.model_manager.validate_outbound_url",
        return_value=("https", "example.com", 443, ["1.1.1.1"]),
    )
    @unittest_run_loop
    async def test_r144_download_create_boundary_matrix(
        self, _mock_validate, _mock_admin
    ):
        case = R144_IO_BOUNDARY_MATRIX["model_manager_download_create"][0]
        resp = await self.client.post(
            "/openclaw/models/downloads",
            json={
                "model_id": "bad-provenance",
                "name": "Bad Provenance",
                "model_type": "checkpoint",
                "source": "catalog",
                "source_label": "Catalog",
                "download_url": "https://example.com/bad-provenance.safetensors",
                "expected_sha256": "a" * 64,
                "provenance": "bad",
            },
        )
        self.assertEqual(resp.status, case["expected_status"])
        body = await resp.json()
        self.assertEqual(body["error"], case["expected_error"])

    @patch("api.model_manager.require_admin_token", return_value=(True, None))
    @patch(
        "services.model_manager.validate_outbound_url",
        return_value=("https", "example.com", 443, ["1.1.1.1"]),
    )
    @unittest_run_loop
    async def test_r144_import_boundary_matrix(self, _mock_validate, _mock_admin):
        task_id = await self._create_completed_task(model_id="boundary-model")
        cases = R144_IO_BOUNDARY_MATRIX["model_manager_import"]
        payloads = {
            "invalid_filename_extension_rejected": {
                "task_id": task_id,
                "filename": "bad.txt",
            },
            "invalid_destination_rejected": {
                "task_id": task_id,
                "destination_subdir": "../escape",
            },
        }
        for case in cases:
            with self.subTest(case=case["case_id"]):
                resp = await self.client.post(
                    "/openclaw/models/import",
                    json=payloads[case["case_id"]],
                )
                self.assertEqual(resp.status, case["expected_status"])
                body = await resp.json()
                self.assertEqual(body["error"], case["expected_error"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
