import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ModuleNotFoundError:
    AIOHTTP_AVAILABLE = False

sys.path.append(os.getcwd())


@unittest.skipIf(not AIOHTTP_AVAILABLE, "aiohttp not available")
class TestPngInfoAPI(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_unauthorized_requests(self):
        from api.pnginfo import pnginfo_handler

        request = AsyncMock()
        with patch("api.pnginfo.require_admin_token", return_value=(False, "Denied")):
            resp = await pnginfo_handler(request)
        self.assertEqual(resp.status, 403)
        self.assertEqual(json.loads(resp.body)["ok"], False)

    async def test_rejects_invalid_json(self):
        from api.pnginfo import pnginfo_handler

        request = AsyncMock()
        request.json = AsyncMock(side_effect=ValueError("bad json"))
        with (
            patch("api.pnginfo.require_admin_token", return_value=(True, None)),
            patch("api.pnginfo.check_rate_limit", return_value=True),
        ):
            resp = await pnginfo_handler(request)
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(resp.body)["error"], "invalid_json")

    async def test_returns_parsed_payload(self):
        from api.pnginfo import pnginfo_handler

        request = AsyncMock()
        request.json = AsyncMock(return_value={"image_b64": "data"})
        expected = {
            "ok": True,
            "source": "a1111",
            "info": "raw infotext",
            "parameters": {"positive_prompt": "cat"},
            "items": {"Comment": "raw infotext"},
        }
        with (
            patch("api.pnginfo.require_admin_token", return_value=(True, None)),
            patch("api.pnginfo.check_rate_limit", return_value=True),
            patch("api.pnginfo.run_in_thread", return_value=expected),
        ):
            resp = await pnginfo_handler(request)
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.body), expected)

    async def test_returns_comfyui_extraction_payload(self):
        from api.pnginfo import pnginfo_handler

        request = AsyncMock()
        request.json = AsyncMock(return_value={"image_b64": "data"})
        expected = {
            "ok": True,
            "source": "comfyui",
            "info": "ComfyUI metadata detected. Extracted prompt and sampler fields from saved graph.",
            "parameters": {
                "positive_prompt": "masterpiece portrait",
                "negative_prompt": "lowres",
                "Steps": 20,
                "Sampler": "euler",
            },
            "items": {"prompt": {"3": {"class_type": "KSampler"}}},
        }
        with (
            patch("api.pnginfo.require_admin_token", return_value=(True, None)),
            patch("api.pnginfo.check_rate_limit", return_value=True),
            patch("api.pnginfo.run_in_thread", return_value=expected),
        ):
            resp = await pnginfo_handler(request)
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.body), expected)

    async def test_returns_contract_error_details(self):
        from api.pnginfo import pnginfo_handler
        from services.pnginfo import PngInfoError

        request = AsyncMock()
        request.json = AsyncMock(return_value={"image_b64": ""})
        with (
            patch("api.pnginfo.require_admin_token", return_value=(True, None)),
            patch("api.pnginfo.check_rate_limit", return_value=True),
            patch(
                "api.pnginfo.run_in_thread",
                side_effect=PngInfoError(
                    "image_b64_required", "image_b64 required", status=400
                ),
            ),
        ):
            resp = await pnginfo_handler(request)
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 400)
        self.assertEqual(body["error"], "image_b64_required")
        self.assertEqual(body["detail"], "image_b64 required")

    async def test_returns_oversize_contract_details(self):
        from api.pnginfo import pnginfo_handler
        from services.pnginfo import PngInfoError

        request = AsyncMock()
        request.json = AsyncMock(return_value={"image_b64": "huge"})
        with (
            patch("api.pnginfo.require_admin_token", return_value=(True, None)),
            patch("api.pnginfo.check_rate_limit", return_value=True),
            patch(
                "api.pnginfo.run_in_thread",
                side_effect=PngInfoError(
                    "image_b64_too_large",
                    "image_b64 exceeds the PNG Info limit (64 MiB). PNG Info must inspect the original metadata-bearing file without browser recompression.",
                    status=400,
                ),
            ),
        ):
            resp = await pnginfo_handler(request)
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 400)
        self.assertEqual(body["error"], "image_b64_too_large")
        self.assertIn("64 MiB", body["detail"])


if __name__ == "__main__":
    unittest.main()
