import base64
import io
import json
import unittest
from unittest.mock import patch

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

    def _make_comfy_png(self, prompt_graph: dict, workflow: dict | None = None) -> str:
        metadata = {"prompt": json.dumps(prompt_graph)}
        if workflow is not None:
            metadata["workflow"] = json.dumps(workflow)
        return self._make_png(metadata)

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
        prompt = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 8,
                    "denoise": 1,
                    "latent_image": ["5", 0],
                    "model": ["4", 0],
                    "negative": ["7", 0],
                    "positive": ["6", 0],
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "seed": 8566257,
                    "steps": 20,
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"height": 512, "width": 512},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["4", 1], "text": "masterpiece best quality girl"},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["4", 1], "text": "bad hands"},
            },
        }
        workflow = {"nodes": [{"id": 3, "type": "KSampler"}]}
        result = parse_image_metadata(self._make_comfy_png(prompt, workflow))
        self.assertEqual(result["source"], "comfyui")
        self.assertIn("ComfyUI metadata detected.", result["info"])
        self.assertEqual(
            result["parameters"]["positive_prompt"], "masterpiece best quality girl"
        )
        self.assertEqual(result["parameters"]["negative_prompt"], "bad hands")
        self.assertEqual(result["parameters"]["Steps"], 20)
        self.assertEqual(result["parameters"]["CFG scale"], 8)
        self.assertEqual(result["parameters"]["Seed"], 8566257)
        self.assertEqual(result["parameters"]["Sampler"], "euler")
        self.assertEqual(result["parameters"]["Scheduler"], "normal")
        self.assertEqual(result["parameters"]["Size"], "512x512")
        self.assertEqual(result["parameters"]["Size-1"], 512)
        self.assertEqual(result["parameters"]["Size-2"], 512)
        self.assertEqual(
            result["parameters"]["Model"], "v1-5-pruned-emaonly.safetensors"
        )
        self.assertIsInstance(result["items"]["prompt"], dict)
        self.assertEqual(result["items"]["workflow"]["nodes"][0]["type"], "KSampler")

    def test_parse_comfyui_ksampler_advanced_and_sdxl_prompts(self):
        prompt = {
            "10": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "cfg": 6.5,
                    "latent_image": ["12", 0],
                    "model": ["11", 0],
                    "negative": ["14", 0],
                    "positive": ["13", 0],
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "noise_seed": 998877,
                    "steps": 30,
                    "denoise": 0.42,
                },
            },
            "11": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "sdxl-base.safetensors"},
            },
            "12": {
                "class_type": "EmptyLatentImage",
                "inputs": {"height": 1024, "width": 1024},
            },
            "13": {
                "class_type": "CLIPTextEncodeSDXL",
                "inputs": {
                    "text_g": "cinematic portrait",
                    "text_l": "sharp details",
                },
            },
            "14": {
                "class_type": "CLIPTextEncodeSDXL",
                "inputs": {
                    "text_g": "blurry",
                    "text_l": "blurry",
                },
            },
        }
        result = parse_image_metadata(self._make_comfy_png(prompt))
        self.assertEqual(result["parameters"]["Seed"], 998877)
        self.assertEqual(result["parameters"]["Steps"], 30)
        self.assertEqual(result["parameters"]["CFG scale"], 6.5)
        self.assertEqual(result["parameters"]["Denoise"], 0.42)
        self.assertEqual(result["parameters"]["Sampler"], "dpmpp_2m")
        self.assertEqual(result["parameters"]["Scheduler"], "karras")
        self.assertEqual(
            result["parameters"]["positive_prompt"],
            "Global: cinematic portrait\nLocal: sharp details",
        )
        self.assertEqual(result["parameters"]["negative_prompt"], "blurry")

    def test_parse_comfyui_flux_prompt_nodes(self):
        prompt = {
            "20": {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 3.5,
                    "positive": ["21", 0],
                    "negative": ["22", 0],
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "seed": 55,
                    "steps": 12,
                },
            },
            "21": {
                "class_type": "CLIPTextEncodeFlux",
                "inputs": {
                    "clip_l": "subject on white backdrop",
                    "t5xxl": "high detail fashion portrait",
                },
            },
            "22": {
                "class_type": "CLIPTextEncodeSDXLRefiner",
                "inputs": {"text": "low quality, anatomy errors"},
            },
        }
        result = parse_image_metadata(self._make_comfy_png(prompt))
        self.assertEqual(
            result["parameters"]["positive_prompt"],
            "CLIP-L: subject on white backdrop\nT5XXL: high detail fashion portrait",
        )
        self.assertEqual(
            result["parameters"]["negative_prompt"], "low quality, anatomy errors"
        )

    def test_parse_comfyui_custom_clip_text_node_ignores_non_prompt_strings(self):
        prompt = {
            "20": {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 7,
                    "positive": ["21", 0],
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "seed": 77,
                    "steps": 24,
                },
            },
            "21": {
                "class_type": "CLIPTextEncodeA1111",
                "inputs": {
                    "text": "masterpiece, best quality, cinematic portrait",
                    "parser": "A1111",
                    "mean_normalization": True,
                    "use_old_emphasis_implementation": False,
                },
            },
        }
        result = parse_image_metadata(self._make_comfy_png(prompt))
        self.assertEqual(
            result["parameters"]["positive_prompt"],
            "masterpiece, best quality, cinematic portrait",
        )

    def test_parse_comfyui_custom_clip_text_node_without_prompt_keys_stays_empty(self):
        prompt = {
            "20": {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 7,
                    "positive": ["21", 0],
                    "sampler_name": "euler",
                    "seed": 77,
                    "steps": 24,
                },
            },
            "21": {
                "class_type": "CLIPTextEncodeCustom",
                "inputs": {
                    "parser": "A1111",
                    "mode": "prompt",
                },
            },
        }
        result = parse_image_metadata(self._make_comfy_png(prompt))
        self.assertNotIn("positive_prompt", result["parameters"])

    def test_parse_comfyui_graph_loop_degrades_to_partial_extraction(self):
        prompt = {
            "30": {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 7,
                    "positive": ["31", 0],
                    "sampler_name": "euler",
                    "seed": 11,
                    "steps": 20,
                },
            },
            "31": {
                "class_type": "ConditioningSetArea",
                "inputs": {
                    "conditioning": ["31", 0],
                },
            },
        }
        result = parse_image_metadata(self._make_comfy_png(prompt))
        self.assertEqual(result["source"], "comfyui")
        self.assertEqual(result["parameters"]["Steps"], 20)
        self.assertNotIn("positive_prompt", result["parameters"])

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

    def test_pnginfo_payload_limit_raises_explicit_error(self):
        with (
            patch("services.pnginfo.MAX_PNGINFO_IMAGE_B64_LEN", 32),
            self.assertRaises(PngInfoError) as ctx,
        ):
            parse_image_metadata(self._make_png({}))
        self.assertEqual(ctx.exception.code, "image_b64_too_large")
        self.assertIn("32 B", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
