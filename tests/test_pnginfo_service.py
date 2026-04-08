import base64
import io
import unittest

try:
    from PIL import Image, PngImagePlugin

    PIL_AVAILABLE = True
except ModuleNotFoundError:
    PIL_AVAILABLE = False

from services.pnginfo import PngInfoError, parse_image_metadata


def _to_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


@unittest.skipIf(not PIL_AVAILABLE, "Pillow not available")
class TestPngInfoService(unittest.TestCase):
    def _make_png(self, metadata: dict[str, str]) -> str:
        image = Image.new("RGB", (8, 8), color="white")
        info = PngImagePlugin.PngInfo()
        for key, value in metadata.items():
            info.add_text(key, value)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", pnginfo=info)
        return _to_b64(buffer.getvalue())

    def _make_jpeg_with_user_comment(self, comment: str) -> str:
        image = Image.new("RGB", (8, 8), color="white")
        exif = Image.Exif()
        exif[37510] = ("ASCII\x00\x00\x00" + comment).encode("utf-8")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", exif=exif)
        return _to_b64(buffer.getvalue())

    def test_parse_a1111_parameters_chunk(self):
        infotext = (
            "masterpiece cat portrait\n"
            "Negative prompt: blur, lowres\n"
            "Steps: 24, Sampler: Euler a, CFG scale: 7, Seed: 42, "
            "Size: 768x512, Model: testModel, Model hash: abc123"
        )
        result = parse_image_metadata(self._make_png({"parameters": infotext}))
        self.assertEqual(result["source"], "a1111")
        self.assertEqual(result["info"], infotext)
        self.assertEqual(
            result["parameters"]["positive_prompt"], "masterpiece cat portrait"
        )
        self.assertEqual(result["parameters"]["negative_prompt"], "blur, lowres")
        self.assertEqual(result["parameters"]["Steps"], "24")
        self.assertEqual(result["parameters"]["Size-1"], 768)
        self.assertEqual(result["parameters"]["Size-2"], 512)

    def test_parse_comment_fallback(self):
        infotext = (
            "cat\nSteps: 12, Sampler: Euler, CFG scale: 6, Seed: 11, Size: 512x512"
        )
        result = parse_image_metadata(self._make_png({"Comment": infotext}))
        self.assertEqual(result["source"], "a1111")
        self.assertEqual(result["parameters"]["positive_prompt"], "cat")
        self.assertEqual(result["parameters"]["Steps"], "12")

    def test_parse_exif_user_comment_fallback(self):
        infotext = (
            "jpeg cat\nSteps: 15, Sampler: Euler, CFG scale: 5, Seed: 7, Size: 640x640"
        )
        result = parse_image_metadata(self._make_jpeg_with_user_comment(infotext))
        self.assertEqual(result["source"], "a1111")
        self.assertEqual(result["info"], infotext)
        self.assertEqual(result["parameters"]["Size"], "640x640")

    def test_parse_comfyui_prompt_and_workflow_metadata(self):
        prompt = (
            '{"1":{"class_type":"KSampler","inputs":{"steps":20,"cfg":7,"seed":123}}}'
        )
        workflow = '{"nodes":[{"id":1,"type":"KSampler"}]}'
        result = parse_image_metadata(
            self._make_png({"prompt": prompt, "workflow": workflow})
        )
        self.assertEqual(result["source"], "comfyui")
        self.assertEqual(result["info"], "ComfyUI metadata detected.")
        self.assertEqual(result["parameters"], {})
        self.assertIsInstance(result["items"]["prompt"], dict)
        self.assertEqual(result["items"]["workflow"]["nodes"][0]["type"], "KSampler")

    def test_parse_unknown_image_without_metadata(self):
        result = parse_image_metadata(self._make_png({}))
        self.assertEqual(result["source"], "unknown")
        self.assertEqual(result["info"], "")
        self.assertEqual(result["parameters"], {})
        self.assertEqual(result["items"], {})

    def test_invalid_base64_raises_contract_error(self):
        with self.assertRaises(PngInfoError) as ctx:
            parse_image_metadata("%%%not-base64%%%")
        self.assertEqual(ctx.exception.code, "invalid_image_b64")


if __name__ == "__main__":
    unittest.main()
