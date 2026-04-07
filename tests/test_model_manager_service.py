import hashlib
import io
import json
import os
import tempfile
import threading
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
from services.safe_io import PathTraversalError


class _FakeResponse:
    def __init__(self, *, code: int, body: bytes, headers: dict[str, str]):
        self._code = int(code)
        self._body = io.BytesIO(body)
        self.headers = headers

    def getcode(self):
        return self._code

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeOpener:
    def __init__(self, mapping):
        self._mapping = mapping

    def open(self, req, timeout=0):  # pragma: no cover - exercised via service
        range_header = req.headers.get("Range") or req.headers.get("range") or ""
        factory = self._mapping.get(range_header) or self._mapping.get("__default__")
        if factory is None:
            raise AssertionError(f"unexpected range header: {range_header!r}")
        return factory()


class TestModelManagerService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_model_manager_service_")
        self.state_root = Path(self.tmp.name) / "state"
        self.install_root = Path(self.tmp.name) / "install"
        self.manager = ModelManager(
            state_root=self.state_root, install_root=self.install_root
        )
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

    @patch(
        "services.model_manager.validate_outbound_url",
        return_value=("https", "example.com", 443, ["1.1.1.1"]),
    )
    def test_create_download_and_import_success(self, _mock_validate):
        payload = b"model-bytes"
        digest = hashlib.sha256(payload).hexdigest()

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
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model-a",
            },
        )
        done = self._wait_terminal(task["task_id"])
        self.assertEqual(done["state"], "completed")
        rec = self.manager.import_downloaded_model(task_id=task["task_id"])
        self.assertEqual(rec["model_id"], "model-a")
        installed = self.install_root / rec["installation_path"]
        self.assertTrue(installed.exists())
        self.assertEqual(installed.read_bytes(), payload)

    @patch(
        "services.model_manager.validate_outbound_url",
        return_value=("https", "example.com", 443, ["1.1.1.1"]),
    )
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
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model-cancel",
            },
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
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model",
            },
            tenant_id="default",
            state="completed",
            staged_path=str(staged_file),
            computed_sha256="a" * 64,
        )
        self.manager._tasks[task.task_id] = task
        with self.assertRaises(ModelManagerError) as ctx:
            self.manager.import_downloaded_model(task_id=task.task_id)
        self.assertEqual(ctx.exception.code, "sha256_mismatch")

    def test_resolve_install_target_rejects_escape(self):
        with self.assertRaises(PathTraversalError):
            self.manager._resolve_install_target(
                str(self.install_root), "../escape.bin"
            )

    def test_import_records_resolved_relative_installation_path(self):
        payload = b"model-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        staged_dir = self.manager.staging_dir / "task-safe-path"
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_file = staged_dir / "model.safetensors"
        staged_file.write_bytes(payload)
        task = DownloadTask(
            task_id="task-safe-path",
            model_id="model-safe-path",
            name="Model Safe Path",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model.safetensors",
            destination_subdir="checkpoints//nested",
            filename="model.safetensors",
            expected_sha256=digest,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model",
            },
            tenant_id="default",
            state="completed",
            staged_path=str(staged_file),
            computed_sha256=digest,
        )
        self.manager._tasks[task.task_id] = task
        rec = self.manager.import_downloaded_model(task_id=task.task_id)
        self.assertEqual(
            rec["installation_path"], "checkpoints/nested/model.safetensors"
        )
        self.assertTrue((self.install_root / rec["installation_path"]).exists())

    def test_import_cleanup_remains_bounded_on_copy_failure(self):
        payload = b"model-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        staged_dir = self.manager.staging_dir / "task-bounded-cleanup"
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_file = staged_dir / "model.safetensors"
        staged_file.write_bytes(payload)
        task = DownloadTask(
            task_id="task-bounded-cleanup",
            model_id="model-bounded-cleanup",
            name="Model Bounded Cleanup",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model.safetensors",
            destination_subdir="checkpoints/nested",
            filename="model.safetensors",
            expected_sha256=digest,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model",
            },
            tenant_id="default",
            state="completed",
            staged_path=str(staged_file),
            computed_sha256=digest,
        )
        self.manager._tasks[task.task_id] = task

        with patch("services.model_manager_transfer.shutil.copy2", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                self.manager.import_downloaded_model(task_id=task.task_id)

        target_dir = self.install_root / "checkpoints" / "nested"
        if target_dir.exists():
            leftovers = [path.name for path in target_dir.iterdir()]
        else:
            leftovers = []
        self.assertEqual(leftovers, [])

    def test_list_download_tasks_delta_cursor_contract(self):
        first = DownloadTask(
            task_id="task-1",
            model_id="model-1",
            name="Model 1",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model-1.safetensors",
            destination_subdir="checkpoints",
            filename="model-1.safetensors",
            expected_sha256="a" * 64,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model-1",
            },
            tenant_id="default",
            created_at=10.0,
            change_seq=4,
        )
        second = DownloadTask(
            task_id="task-2",
            model_id="model-2",
            name="Model 2",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model-2.safetensors",
            destination_subdir="checkpoints",
            filename="model-2.safetensors",
            expected_sha256="b" * 64,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model-2",
            },
            tenant_id="default",
            created_at=11.0,
            change_seq=5,
        )
        self.manager._tasks[first.task_id] = first
        self.manager._tasks[second.task_id] = second
        self.manager._task_change_seq = 5

        result = self.manager.list_download_tasks(limit=10, since_seq=4)
        self.assertEqual([row["task_id"] for row in result["tasks"]], ["task-2"])
        self.assertEqual(result["delta"]["requested_since_seq"], 4)
        self.assertEqual(result["delta"]["effective_since_seq"], 4)
        self.assertEqual(result["delta"]["next_since_seq"], 5)
        self.assertEqual(result["delta"]["cursor_status"], "ok")
        self.assertFalse(result["delta"]["truncated"])

    def test_list_download_tasks_delta_resets_stale_cursor(self):
        first = DownloadTask(
            task_id="task-1",
            model_id="model-1",
            name="Model 1",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model-1.safetensors",
            destination_subdir="checkpoints",
            filename="model-1.safetensors",
            expected_sha256="a" * 64,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model-1",
            },
            tenant_id="default",
            created_at=10.0,
            change_seq=7,
        )
        second = DownloadTask(
            task_id="task-2",
            model_id="model-2",
            name="Model 2",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model-2.safetensors",
            destination_subdir="checkpoints",
            filename="model-2.safetensors",
            expected_sha256="b" * 64,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model-2",
            },
            tenant_id="default",
            created_at=11.0,
            change_seq=8,
        )
        self.manager._tasks[first.task_id] = first
        self.manager._tasks[second.task_id] = second
        self.manager._task_change_seq = 8

        result = self.manager.list_download_tasks(limit=1, since_seq=1)
        self.assertEqual([row["task_id"] for row in result["tasks"]], ["task-1"])
        self.assertEqual(result["delta"]["effective_since_seq"], 6)
        self.assertEqual(result["delta"]["next_since_seq"], 7)
        self.assertEqual(result["delta"]["cursor_status"], "stale_cursor_reset")
        self.assertTrue(result["delta"]["truncated"])

    @patch(
        "services.model_manager.validate_outbound_url",
        return_value=("https", "example.com", 443, ["1.1.1.1"]),
    )
    @patch("services.model_manager._build_pinned_opener")
    def test_resume_download_with_http_range(self, mock_opener, _mock_validate):
        payload = b"123456789"
        digest = hashlib.sha256(payload).hexdigest()
        task = DownloadTask(
            task_id="task-resume",
            model_id="model-resume",
            name="Model Resume",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model-resume.safetensors",
            destination_subdir="checkpoints",
            filename="model-resume.safetensors",
            expected_sha256=digest,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model-resume",
            },
            tenant_id="default",
            state="running",
        )
        self.manager._tasks[task.task_id] = task
        self.manager._cancel_events[task.task_id] = threading.Event()

        stage_dir = self.manager.staging_dir / task.task_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        part = stage_dir / f"{task.filename}.part"
        part.write_bytes(payload[:4])
        checkpoint = self.manager._checkpoint_path(part)
        checkpoint.write_text(
            json.dumps(
                {
                    "version": 1,
                    "task_id": task.task_id,
                    "download_url": task.download_url,
                    "expected_sha256": task.expected_sha256,
                    "filename": task.filename,
                    "bytes_downloaded": 4,
                    "etag": "etag-1",
                    "last_modified": "lm-1",
                }
            ),
            encoding="utf-8",
        )

        mock_opener.return_value = _FakeOpener(
            {
                "bytes=4-": lambda: _FakeResponse(
                    code=206,
                    body=payload[4:],
                    headers={
                        "Content-Range": "bytes 4-8/9",
                        "Content-Length": "5",
                        "ETag": "etag-1",
                        "Last-Modified": "lm-1",
                    },
                )
            }
        )

        final_path, got = self.manager._download(task, threading.Event())
        self.assertEqual(got, digest)
        self.assertEqual(Path(final_path).read_bytes(), payload)
        self.assertFalse(checkpoint.exists())
        self.assertEqual(
            self.manager._tasks[task.task_id].resume_status, "resumed_partial"
        )

    @patch(
        "services.model_manager.validate_outbound_url",
        return_value=("https", "example.com", 443, ["1.1.1.1"]),
    )
    @patch("services.model_manager._build_pinned_opener")
    def test_resume_fallback_when_range_not_supported(
        self, mock_opener, _mock_validate
    ):
        payload = b"abcdefghij"
        digest = hashlib.sha256(payload).hexdigest()
        task = DownloadTask(
            task_id="task-resume-fallback",
            model_id="model-resume-fallback",
            name="Model Resume Fallback",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/model-resume-fallback.safetensors",
            destination_subdir="checkpoints",
            filename="model-resume-fallback.safetensors",
            expected_sha256=digest,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/model-resume-fallback",
            },
            tenant_id="default",
            state="running",
        )
        self.manager._tasks[task.task_id] = task
        self.manager._cancel_events[task.task_id] = threading.Event()

        stage_dir = self.manager.staging_dir / task.task_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        part = stage_dir / f"{task.filename}.part"
        part.write_bytes(payload[:3])
        checkpoint = self.manager._checkpoint_path(part)
        checkpoint.write_text(
            json.dumps(
                {
                    "version": 1,
                    "task_id": task.task_id,
                    "download_url": task.download_url,
                    "expected_sha256": task.expected_sha256,
                    "filename": task.filename,
                    "bytes_downloaded": 3,
                    "etag": "etag-1",
                    "last_modified": "lm-1",
                }
            ),
            encoding="utf-8",
        )

        mock_opener.return_value = _FakeOpener(
            {
                "bytes=3-": lambda: _FakeResponse(
                    code=200,
                    body=payload,
                    headers={
                        "Content-Length": str(len(payload)),
                        "ETag": "etag-1",
                        "Last-Modified": "lm-1",
                    },
                ),
                "__default__": lambda: _FakeResponse(
                    code=200,
                    body=payload,
                    headers={
                        "Content-Length": str(len(payload)),
                        "ETag": "etag-1",
                        "Last-Modified": "lm-1",
                    },
                ),
            }
        )

        final_path, got = self.manager._download(task, threading.Event())
        self.assertEqual(got, digest)
        self.assertEqual(Path(final_path).read_bytes(), payload)
        self.assertFalse(checkpoint.exists())
        self.assertEqual(
            self.manager._tasks[task.task_id].resume_status,
            "resume_fallback_range_not_supported",
        )

    def test_restart_recovery_replay_limit(self):
        state_root = Path(self.tmp.name) / "recover-state"
        install_root = Path(self.tmp.name) / "recover-install"
        state_root.mkdir(parents=True, exist_ok=True)

        t1 = DownloadTask(
            task_id="recover-1",
            model_id="m1",
            name="Recover 1",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/m1.safetensors",
            destination_subdir="checkpoints",
            filename="m1.safetensors",
            expected_sha256="a" * 64,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/m1",
            },
            tenant_id="default",
            state="running",
        )
        t2 = DownloadTask(
            task_id="recover-2",
            model_id="m2",
            name="Recover 2",
            model_type="checkpoint",
            source="catalog",
            source_label="Catalog",
            download_url="https://example.com/m2.safetensors",
            destination_subdir="checkpoints",
            filename="m2.safetensors",
            expected_sha256="b" * 64,
            provenance={
                "publisher": "OpenClaw",
                "license": "OpenRAIL",
                "source_url": "https://example.com/m2",
            },
            tenant_id="default",
            state="queued",
        )
        (state_root / "download_tasks.json").write_text(
            json.dumps([t1.to_dict(), t2.to_dict()]), encoding="utf-8"
        )

        with patch.object(ModelManager, "_run_task", return_value=None):
            with patch.dict(
                os.environ,
                {"OPENCLAW_MODEL_DOWNLOAD_RECOVERY_REPLAY_LIMIT": "1"},
                clear=False,
            ):
                manager = ModelManager(state_root=state_root, install_root=install_root)
                r1 = manager.get_download_task("recover-1")
                r2 = manager.get_download_task("recover-2")
                manager._executor.shutdown(wait=True)

        states = {r1["state"], r2["state"]}
        self.assertIn("queued", states)
        self.assertIn("failed", states)
        failed = r1 if r1["state"] == "failed" else r2
        replay = r1 if r1["state"] == "queued" else r2
        self.assertEqual(failed["error"], "recovery_replay_limit_exceeded")
        self.assertEqual(replay["resume_status"], "restart_replay_queued")
        self.assertEqual(replay["recovery_attempts"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
