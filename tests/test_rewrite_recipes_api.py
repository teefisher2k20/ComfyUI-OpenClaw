import tempfile
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

from api.rewrite_recipes import (
    rewrite_recipe_apply_handler,
    rewrite_recipe_create_handler,
    rewrite_recipe_delete_handler,
    rewrite_recipe_dry_run_handler,
    rewrite_recipe_get_handler,
    rewrite_recipe_update_handler,
    rewrite_recipes_list_handler,
)
from services.rewrite_recipes import rewrite_recipe_store


@unittest.skipIf(web is None, "aiohttp not installed")
class TestRewriteRecipesAPI(AioHTTPTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_rewrite_recipes_api_")
        self._orig_storage_dir = rewrite_recipe_store.storage_dir
        rewrite_recipe_store.storage_dir = Path(self.tmp.name)
        rewrite_recipe_store.storage_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        rewrite_recipe_store.storage_dir = self._orig_storage_dir
        self.tmp.cleanup()
        super().tearDown()

    async def get_application(self):
        app = web.Application()
        app.router.add_get("/openclaw/rewrite/recipes", rewrite_recipes_list_handler)
        app.router.add_post("/openclaw/rewrite/recipes", rewrite_recipe_create_handler)
        app.router.add_get(
            "/openclaw/rewrite/recipes/{recipe_id}", rewrite_recipe_get_handler
        )
        app.router.add_put(
            "/openclaw/rewrite/recipes/{recipe_id}", rewrite_recipe_update_handler
        )
        app.router.add_delete(
            "/openclaw/rewrite/recipes/{recipe_id}", rewrite_recipe_delete_handler
        )
        app.router.add_post(
            "/openclaw/rewrite/recipes/{recipe_id}/dry-run",
            rewrite_recipe_dry_run_handler,
        )
        app.router.add_post(
            "/openclaw/rewrite/recipes/{recipe_id}/apply",
            rewrite_recipe_apply_handler,
        )
        return app

    @patch("api.rewrite_recipes.require_admin_token", return_value=(True, None))
    @unittest_run_loop
    async def test_crud_dry_run_and_apply(self, _mock_admin):
        create_resp = await self.client.post(
            "/openclaw/rewrite/recipes",
            json={
                "name": "rewrite prompt",
                "operations": [{"path": "/1/inputs/text", "value": "{{topic}}"}],
                "constraints": {"required_inputs": ["topic"]},
                "tags": ["test"],
            },
        )
        self.assertEqual(create_resp.status, 201)
        create_body = await create_resp.json()
        self.assertTrue(create_body["ok"])
        recipe_id = create_body["recipe"]["id"]

        list_resp = await self.client.get("/openclaw/rewrite/recipes")
        self.assertEqual(list_resp.status, 200)
        list_body = await list_resp.json()
        self.assertTrue(list_body["ok"])
        self.assertEqual(len(list_body["recipes"]), 1)

        workflow = {"1": {"inputs": {"text": "old"}}}
        dry_run_resp = await self.client.post(
            f"/openclaw/rewrite/recipes/{recipe_id}/dry-run",
            json={"workflow": workflow, "inputs": {"topic": "new-topic"}},
        )
        self.assertEqual(dry_run_resp.status, 200)
        dry_run_body = await dry_run_resp.json()
        self.assertTrue(dry_run_body["ok"])
        self.assertEqual(dry_run_body["workflow"]["1"]["inputs"]["text"], "new-topic")
        self.assertGreaterEqual(len(dry_run_body["diff"]), 1)

        apply_guard_resp = await self.client.post(
            f"/openclaw/rewrite/recipes/{recipe_id}/apply",
            json={"workflow": workflow, "inputs": {"topic": "new-topic"}},
        )
        self.assertEqual(apply_guard_resp.status, 400)
        apply_guard_body = await apply_guard_resp.json()
        self.assertEqual(apply_guard_body["error"], "apply_requires_confirm")
        self.assertEqual(apply_guard_body["rollback_snapshot"], workflow)

        apply_resp = await self.client.post(
            f"/openclaw/rewrite/recipes/{recipe_id}/apply",
            json={
                "workflow": workflow,
                "inputs": {"topic": "new-topic"},
                "confirm": True,
            },
        )
        self.assertEqual(apply_resp.status, 200)
        apply_body = await apply_resp.json()
        self.assertTrue(apply_body["ok"])
        self.assertEqual(
            apply_body["applied_workflow"]["1"]["inputs"]["text"], "new-topic"
        )

    @patch("api.rewrite_recipes.require_admin_token", return_value=(True, None))
    @unittest_run_loop
    async def test_apply_failure_returns_rollback_snapshot(self, _mock_admin):
        create_resp = await self.client.post(
            "/openclaw/rewrite/recipes",
            json={
                "name": "bad recipe",
                "operations": [{"path": "/missing/path", "value": "x"}],
            },
        )
        self.assertEqual(create_resp.status, 201)
        recipe_id = (await create_resp.json())["recipe"]["id"]

        workflow = {"1": {"inputs": {"text": "old"}}}
        resp = await self.client.post(
            f"/openclaw/rewrite/recipes/{recipe_id}/apply",
            json={"workflow": workflow, "inputs": {}, "confirm": True},
        )
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertEqual(body["error"], "validation_error")
        self.assertEqual(body["rollback_snapshot"], workflow)

    @patch(
        "api.rewrite_recipes.require_admin_token",
        return_value=(False, "invalid_admin_token"),
    )
    @unittest_run_loop
    async def test_admin_gating(self, _mock_admin):
        resp = await self.client.get("/openclaw/rewrite/recipes")
        self.assertEqual(resp.status, 403)


if __name__ == "__main__":
    unittest.main()

