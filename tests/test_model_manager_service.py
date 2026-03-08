import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from services.model_manager import (
    DownloadCancelled,
    DownloadTask,
    ModelManager,
    ModelManagerError,
)


class TestModelManagerService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_model_manager_service_")
        self.state_root = Path(self.tmp.name) / "state"
        self.install_root = Path(self.tmp.name) / "install"
        self.manager = ModelManager(state_root=self.state_root, install_root=self.install_root)
        self.manager.allow_any_public = True

    def tearDown(self):
        self.tmp.cleanup()

    def _wait_terminal(self, task_id: str, timeout: float = 3.0):
        end = time.time() + timeout
        while time.time() < end:
            task = self.manager.get_download_task(task_id)
            if task["state"] in {"completed", "failed", "cancelled"}:
                return task
            time.sleep(0.02)
        self.fail(f"Task {task_id} did not reach terminal state")

    def test_search_filters_and_deterministic_order(self):
        self.manager._save_installations(
            [
                {
                    "id": "rec-a",
                    "model_id": "installed-a",
                    "name": "Installed A",
                    "model_type": "checkpoint",
                    "source": "manual",
                    "source_label": "Manual",
                    "sha256": "a" * 64,
                    "tenant_id": "default",
                    "installed_at": 10,
                },
                {
                    "id": "rec-b",
                    "model_id": "installed-b",
                    "name": "Installed B",
                    "model_type": "lora",
                    "source": "manual",
                    "source_label": "Manual",
                    "sha256": "b" * 64,
                    "tenant_id": "default",
                    "installed_at": 9,
                },
            ]
        )
        catalog_dir = self.state_root / "catalog"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        (catalog_dir / "test.json").write_text(
            """
{
  "source": "catalog",
  "source_label": "Catalog",
  "items": [
    {
      "id": "catalog-a",
      "name": "Catalog A",
      "model_type": "checkpoint",
      "download_url": "https://example.com/catalog-a.safetensors",
      "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    }
  ]
}
""".strip(),
            encoding="utf-8",
        )
        result = self.manager.search_models(limit=10, offset=0)
        self.assertEqual(result["pagination"]["total"], 3)
        names = [item["name"] for item in result["items"]]
        self.assertEqual(names, ["Installed A", "Installed B", "Catalog A"])

        lora_only = self.manager.search_models(model_type="lora")
        self.assertEqual(lora_only["pagination"]["total"], 1)
        self.assertEqual(lora_only["items"][0]["id"], "installed-b")

    @patch("services.model_manager.validate_outbound_url", return_value=("https", "example.com", 443, ["1.1.1.1"]))
    def test_create_download_and_import_success(self, _mock_validate):
        payload = b"model-bytes"
        digest = __import__("hashlib").sha256(payload).hexdigest()

        def fake_download(task, _cancel_event):
            stage = self.manager.staging_dir / task.task_id
            stage.mkdir(parents=True, exist_ok=True)
            final = stage / task.filename
            final.write_bytes(payload)
            return str(final), digest

        self.manager._download = fake_download  # type: ignore[assignment]
        task = self.manager.create_download_task(
            model_id="model-a",
            name="Model A",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model-a.safetensors",
            expected_sha256=digest,
            provenance={"publisher": "OpenClaw", "license": "OpenRAIL", "source_url": "https://example.com/model-a"},
        )
        done = self._wait_terminal(task["task_id"])
        self.assertEqual(done["state"], "completed")
        rec = self.manager.import_downloaded_model(task_id=task["task_id"])
        self.assertEqual(rec["model_id"], "model-a")
        installed = self.install_root / rec["installation_path"]
        self.assertTrue(installed.exists())
        self.assertEqual(installed.read_bytes(), payload)

    @patch("services.model_manager.validate_outbound_url", return_value=("https", "example.com", 443, ["1.1.1.1"]))
    def test_cancel_running_task(self, _mock_validate):
        digest = "d" * 64

        def slow_download(_task, cancel_event):
            for _ in range(60):
                if cancel_event.is_set():
                    raise DownloadCancelled()
                time.sleep(0.01)
            raise AssertionError("expected cancellation")

        self.manager._download = slow_download  # type: ignore[assignment]
        task = self.manager.create_download_task(
            model_id="model-cancel",
            name="Model Cancel",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model-cancel.safetensors",
            expected_sha256=digest,
            provenance={"publisher": "OpenClaw", "license": "OpenRAIL", "source_url": "https://example.com/model-cancel"},
        )
        self.manager.cancel_download_task(task["task_id"])
        done = self._wait_terminal(task["task_id"])
        self.assertEqual(done["state"], "cancelled")

    def test_import_fails_on_hash_mismatch(self):
        staged_dir = self.manager.staging_dir / "task-hash"
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_file = staged_dir / "model.safetensors"
        staged_file.write_bytes(b"bad")
        task = DownloadTask(
            task_id="task-hash",
            model_id="model-hash",
            name="Model Hash",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model.safetensors",
            destination_subdir="checkpoints",
            filename="model.safetensors",
            expected_sha256="a" * 64,
            provenance={"publisher": "OpenClaw", "license": "OpenRAIL", "source_url": "https://example.com/model"},
            tenant_id="default",
            state="completed",
            staged_path=str(staged_file),
            computed_sha256="a" * 64,
        )
        self.manager._tasks[task.task_id] = task
        with self.assertRaises(ModelManagerError) as ctx:
            self.manager.import_downloaded_model(task_id=task.task_id)
        self.assertEqual(ctx.exception.code, "sha256_mismatch")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
