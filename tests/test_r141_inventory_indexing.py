import asyncio
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import services.preflight

try:
    from aiohttp import web
    from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
except Exception:  # pragma: no cover
    web = None  # type: ignore
    AioHTTPTestCase = unittest.TestCase  # type: ignore

    def unittest_run_loop(fn):  # type: ignore
        return fn


def _wait_for(predicate, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestR141InventoryIndexing(unittest.TestCase):
    def setUp(self):
        services.preflight._reset_inventory_state_for_tests()

    def tearDown(self):
        services.preflight._reset_inventory_state_for_tests()

    def test_snapshot_response_schedules_background_refresh(self):
        started = threading.Event()
        release = threading.Event()

        def slow_scan(_checkpoint=None):
            started.set()
            release.wait(timeout=1.0)
            return {"checkpoints": ["sdxl.safetensors"]}

        with (
            patch.object(
                services.preflight, "folder_paths", MagicMock(), create=True
            ) as mock_folder_paths,
            patch.object(services.preflight, "_scan_model_inventory", side_effect=slow_scan),
        ):
            mock_folder_paths.folder_names_and_paths = {"checkpoints": [("/tmp", None)]}

            first = services.preflight.get_model_inventory_snapshot()
            self.assertEqual(first["models"], {})
            self.assertEqual(first["scan_state"], "refreshing")
            self.assertTrue(first["stale"])
            self.assertIsNone(first["snapshot_ts"])
            self.assertTrue(started.wait(timeout=0.5))

            release.set()
            self.assertTrue(
                _wait_for(
                    lambda: services.preflight.get_model_inventory_snapshot(
                        trigger_refresh=False
                    )["scan_state"]
                    == "idle"
                )
            )

            final = services.preflight.get_model_inventory_snapshot(trigger_refresh=False)
            self.assertEqual(
                final["models"], {"checkpoints": ["sdxl.safetensors"]}
            )
            self.assertEqual(final["scan_state"], "idle")
            self.assertFalse(final["stale"])
            self.assertIsNone(final["last_error"])
            self.assertIsInstance(final["snapshot_ts"], float)

    def test_failed_background_refresh_surfaces_error_metadata(self):
        started = threading.Event()

        def exploding_scan(_checkpoint=None):
            started.set()
            raise RuntimeError("scan boom")

        with (
            patch.object(
                services.preflight, "folder_paths", MagicMock(), create=True
            ) as mock_folder_paths,
            patch.object(
                services.preflight, "_scan_model_inventory", side_effect=exploding_scan
            ),
        ):
            mock_folder_paths.folder_names_and_paths = {"checkpoints": [("/tmp", None)]}

            first = services.preflight.get_model_inventory_snapshot()
            self.assertEqual(first["scan_state"], "refreshing")
            self.assertTrue(started.wait(timeout=0.5))
            self.assertTrue(
                _wait_for(
                    lambda: services.preflight.get_model_inventory_snapshot(
                        trigger_refresh=False
                    )["scan_state"]
                    == "error"
                )
            )

            final = services.preflight.get_model_inventory_snapshot(trigger_refresh=False)
            self.assertEqual(final["models"], {})
            self.assertEqual(final["scan_state"], "error")
            self.assertTrue(final["stale"])
            self.assertEqual(final["last_error"], "scan boom")
            self.assertIsNone(final["snapshot_ts"])

    def test_error_state_respects_retry_cooldown(self):
        with patch.object(
            services.preflight, "folder_paths", MagicMock(), create=True
        ) as mock_folder_paths:
            mock_folder_paths.folder_names_and_paths = {"checkpoints": [("/tmp", None)]}
            services.preflight._CACHE[services.preflight._INVENTORY_SCAN_STATE_KEY] = "error"
            services.preflight._CACHE[services.preflight._INVENTORY_LAST_ERROR_KEY] = (
                "scan boom"
            )
            services.preflight._CACHE[services.preflight._INVENTORY_LAST_ATTEMPT_TS_KEY] = (
                time.time()
            )

            with patch.object(
                services.preflight, "_schedule_inventory_refresh_locked"
            ) as mock_schedule:
                snapshot = services.preflight.get_model_inventory_snapshot()

            self.assertEqual(snapshot["scan_state"], "error")
            self.assertTrue(snapshot["stale"])
            self.assertEqual(snapshot["last_error"], "scan boom")
            mock_schedule.assert_not_called()


@unittest.skipIf(web is None, "aiohttp not installed")
class TestR141InventoryApi(AioHTTPTestCase):
    async def get_application(self):
        from api.preflight_handler import inventory_handler

        app = web.Application()
        app.router.add_get("/openclaw/preflight/inventory", inventory_handler)
        return app

    def setUp(self):
        super().setUp()
        services.preflight._reset_inventory_state_for_tests()

    def tearDown(self):
        services.preflight._reset_inventory_state_for_tests()
        super().tearDown()

    @patch("api.preflight_handler.check_rate_limit")
    @patch("api.preflight_handler.require_admin_token")
    @unittest_run_loop
    async def test_inventory_handler_returns_metadata_fields(
        self, mock_require_admin, mock_rate_limit
    ):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)

        with (
            patch.object(
                services.preflight,
                "get_model_inventory_snapshot",
                return_value={
                    "models": {"checkpoints": ["a.safetensors"]},
                    "snapshot_ts": 123.0,
                    "scan_state": "idle",
                    "stale": False,
                    "last_error": None,
                },
            ),
            patch("api.preflight_handler.get_model_inventory_snapshot") as mock_snapshot,
            patch("api.preflight_handler._get_node_class_mappings") as mock_nodes,
        ):
            mock_snapshot.return_value = {
                "models": {"checkpoints": ["a.safetensors"]},
                "snapshot_ts": 123.0,
                "scan_state": "idle",
                "stale": False,
                "last_error": None,
            }
            mock_nodes.return_value = {"KSampler": object}

            resp = await self.client.get("/openclaw/preflight/inventory")
            self.assertEqual(resp.status, 200)
            payload = await resp.json()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["nodes"], ["KSampler"])
        self.assertEqual(payload["models"], {"checkpoints": ["a.safetensors"]})
        self.assertEqual(payload["snapshot_ts"], 123.0)
        self.assertEqual(payload["scan_state"], "idle")
        self.assertFalse(payload["stale"])
        self.assertIsNone(payload["last_error"])

    @patch("api.preflight_handler.check_rate_limit")
    @patch("api.preflight_handler.require_admin_token")
    @unittest_run_loop
    async def test_inventory_handler_returns_quickly_while_refresh_runs(
        self, mock_require_admin, mock_rate_limit
    ):
        mock_rate_limit.return_value = True
        mock_require_admin.return_value = (True, None)
        started = threading.Event()
        release = threading.Event()

        def slow_scan(_checkpoint=None):
            started.set()
            release.wait(timeout=1.0)
            return {"checkpoints": ["late.safetensors"]}

        with (
            patch.object(
                services.preflight, "folder_paths", MagicMock(), create=True
            ) as mock_folder_paths,
            patch.object(services.preflight, "_scan_model_inventory", side_effect=slow_scan),
            patch("api.preflight_handler._get_node_class_mappings", return_value={}),
        ):
            mock_folder_paths.folder_names_and_paths = {"checkpoints": [("/tmp", None)]}

            resp = await asyncio.wait_for(
                self.client.get("/openclaw/preflight/inventory"), timeout=0.2
            )
            payload = await resp.json()

            self.assertTrue(started.wait(timeout=0.5))
            self.assertEqual(resp.status, 200)
            self.assertEqual(payload["models"], {})
            self.assertEqual(payload["scan_state"], "refreshing")
            self.assertTrue(payload["stale"])
            self.assertIsNone(payload["snapshot_ts"])

            release.set()
            self.assertTrue(
                _wait_for(
                    lambda: services.preflight.get_model_inventory_snapshot(
                        trigger_refresh=False
                    )["scan_state"]
                    == "idle"
                )
            )
