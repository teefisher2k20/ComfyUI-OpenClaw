"""
Tests for ComfyUI History Parsing (F17).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestComfyUIHistoryParsing(unittest.TestCase):
    """Test extract_images parses history correctly."""

    def test_extract_images_basic(self):
        from services.comfyui_history import extract_images

        # Minimal history item fixture
        history_item = {
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "test_001.png", "subfolder": "", "type": "output"},
                        {
                            "filename": "test_002.png",
                            "subfolder": "subfolder1",
                            "type": "output",
                        },
                    ]
                },
                "12": {
                    "images": [
                        {"filename": "another.jpg", "subfolder": "", "type": "temp"},
                    ]
                },
            }
        }

        images = extract_images(history_item)

        self.assertEqual(len(images), 3)

        # Check first image
        img1 = images[0]
        self.assertEqual(img1["filename"], "test_001.png")
        self.assertEqual(img1["subfolder"], "")
        self.assertEqual(img1["type"], "output")
        self.assertIn("filename=test_001.png", img1["view_url"])
        self.assertIn("type=output", img1["view_url"])

        # Check subfolder is URL encoded
        img2 = images[1]
        self.assertEqual(img2["subfolder"], "subfolder1")
        self.assertIn("subfolder=subfolder1", img2["view_url"])

    def test_extract_images_empty(self):
        from services.comfyui_history import extract_images

        history_item = {"outputs": {}}
        images = extract_images(history_item)
        self.assertEqual(len(images), 0)

    def test_extract_images_no_filename(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "1": {
                    "images": [
                        {"subfolder": "", "type": "output"},  # Missing filename
                    ]
                }
            }
        }
        images = extract_images(history_item)
        self.assertEqual(len(images), 0)

    def test_extract_images_prefers_asset_hash_view_url(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "2": {
                    "images": [
                        {
                            "filename": "preview.png",
                            "subfolder": "nested",
                            "type": "temp",
                            "asset_hash": "blake3:abc123",
                        }
                    ]
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["filename"], "preview.png")
        self.assertEqual(images[0]["type"], "temp")
        self.assertEqual(images[0]["asset_hash"], "blake3:abc123")
        self.assertIn("filename=blake3%3Aabc123", images[0]["view_url"])
        self.assertNotIn("subfolder=nested", images[0]["view_url"])
        self.assertNotIn("type=temp", images[0]["view_url"])

    def test_get_job_status(self):
        from services.comfyui_history import get_job_status

        # None -> pending
        self.assertEqual(get_job_status(None), "pending")

        # With outputs -> completed
        self.assertEqual(get_job_status({"outputs": {"1": {}}}), "completed")

        # Empty -> unknown
        self.assertEqual(get_job_status({}), "unknown")

        # Explicit status
        self.assertEqual(
            get_job_status({"status": {"status_str": "success"}}), "completed"
        )
        self.assertEqual(get_job_status({"status": {"status_str": "error"}}), "error")


if __name__ == "__main__":
    unittest.main()
