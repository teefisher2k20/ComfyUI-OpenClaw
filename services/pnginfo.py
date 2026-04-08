"""
PNG Info metadata parsing service (R168, R169).
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

# CRITICAL: keep Pillow optional at import time so route bootstrap still loads
# in environments where image parsing deps are not installed.
try:
    from PIL import ExifTags, Image  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    ExifTags = None  # type: ignore
    Image = None  # type: ignore

MAX_PNGINFO_IMAGE_B64_LEN = 64 * 1024 * 1024  # 64 MiB base64 payload ceiling
_RE_PARAM = re.compile(r'\s*(\w[\w \-/]+):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)')
_RE_IMAGE_SIZE = re.compile(r"^(\d+)x(\d+)$")
_COMFYUI_KEYS = ("prompt", "workflow")
_COMFYUI_SAMPLER_TYPES = {"KSampler", "KSamplerAdvanced"}
_A1111_INFO_KEYS = (
    "parameters",
    "comment",
    "Comment",
    "description",
    "Description",
    "UserComment",
    "user_comment",
)


class PngInfoError(Exception):
    def __init__(self, code: str, detail: str, status: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def pnginfo_available() -> bool:
    return Image is not None


def parse_image_metadata(image_b64: str) -> dict[str, Any]:
    if not pnginfo_available():
        raise PngInfoError(
            "pnginfo_unavailable",
            "Pillow (PIL) is required for PNG Info parsing.",
            status=503,
        )

    payload = _decode_image_b64(image_b64)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            text_items = _collect_text_items(image)
    except PngInfoError:
        raise
    except Exception as exc:
        raise PngInfoError("invalid_image", "Unable to decode image metadata.") from exc

    infotext = _extract_a1111_infotext(text_items)
    comfy_items = {
        key: value
        for key, value in text_items.items()
        if key in _COMFYUI_KEYS and value
    }

    if infotext:
        source = "a1111"
        info = infotext
        parameters = _parse_generation_parameters(infotext)
    elif comfy_items:
        source = "comfyui"
        parameters = _extract_comfyui_parameters(comfy_items)
        if parameters:
            info = "ComfyUI metadata detected. Extracted prompt and sampler fields from saved graph."
        else:
            info = "ComfyUI metadata detected. Structured prompt fields were not recoverable from the saved graph."
    else:
        source = "unknown"
        info = ""
        parameters = {}

    items = {
        key: value
        for key, value in text_items.items()
        if value not in (None, "", {}) and key != "parameters"
    }

    return {
        "ok": True,
        "source": source,
        "info": info,
        "parameters": parameters,
        "items": items,
    }


def _decode_image_b64(image_b64: str) -> bytes:
    if not isinstance(image_b64, str) or not image_b64.strip():
        raise PngInfoError("image_b64_required", "image_b64 required")

    value = _sanitize_b64_payload(image_b64)
    if not value:
        raise PngInfoError("image_b64_required", "image_b64 required")

    if len(value) > MAX_PNGINFO_IMAGE_B64_LEN:
        raise PngInfoError(
            "image_b64_too_large",
            "image_b64 exceeds the PNG Info limit "
            f"({_format_bytes(MAX_PNGINFO_IMAGE_B64_LEN)}). "
            "PNG Info must inspect the original metadata-bearing file without browser recompression.",
        )

    padding = len(value) % 4
    if padding:
        value += "=" * (4 - padding)
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise PngInfoError(
            "invalid_image_b64", "image_b64 is not valid base64"
        ) from exc


def _collect_text_items(image) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for key, value in getattr(image, "info", {}).items():
        if key == "exif":
            continue
        normalized = _normalize_metadata_value(value)
        if normalized not in (None, ""):
            items[str(key)] = normalized

    for key, value in _collect_exif_items(image).items():
        items.setdefault(key, value)

    return items


def _collect_exif_items(image) -> dict[str, Any]:
    if ExifTags is None or not hasattr(image, "getexif"):
        return {}

    try:
        exif = image.getexif()
    except Exception:
        return {}
    if not exif:
        return {}

    tag_lookup = getattr(ExifTags, "TAGS", {}) or {}
    result: dict[str, Any] = {}
    for tag_id, value in exif.items():
        key = str(tag_lookup.get(tag_id, tag_id))
        normalized = _normalize_exif_value(key, value)
        if normalized not in (None, ""):
            result[key] = normalized
    return result


def _sanitize_b64_payload(image_b64: str) -> str:
    value = image_b64.strip()
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    return re.sub(r"\s+", "", value)


def _normalize_exif_value(key: str, value: Any) -> Any:
    if isinstance(value, bytes):
        if key == "UserComment":
            return _decode_user_comment(value)
        return value.decode("utf-8", errors="replace").strip("\x00")
    return _normalize_metadata_value(value)


def _decode_user_comment(value: bytes) -> str:
    prefixes = (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"JIS\x00\x00\x00\x00\x00")
    payload = value
    for prefix in prefixes:
        if value.startswith(prefix):
            payload = value[len(prefix) :]
            break
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return payload.decode(encoding).strip("\x00").strip()
        except Exception:
            continue
    return payload.decode("utf-8", errors="replace").strip("\x00").strip()


def _normalize_metadata_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00")
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return ""
    if text[:1] in {"{", "["}:
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


def _extract_a1111_infotext(text_items: dict[str, Any]) -> str:
    for key in _A1111_INFO_KEYS:
        value = text_items.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_generation_parameters(info_text: str) -> dict[str, Any]:
    text = (info_text or "").strip()
    if not text:
        return {}

    lines = [line.rstrip() for line in text.splitlines()]
    params_line = lines[-1] if lines else ""
    prompt_lines = lines[:-1]

    if not _RE_PARAM.search(params_line):
        return {"positive_prompt": text}

    positive_lines: list[str] = []
    negative_lines: list[str] = []
    in_negative = False
    for line in prompt_lines:
        if line.startswith("Negative prompt:"):
            in_negative = True
            negative_lines.append(line[len("Negative prompt:") :].lstrip())
            continue
        if in_negative:
            negative_lines.append(line)
        else:
            positive_lines.append(line)

    parsed: dict[str, Any] = {
        "positive_prompt": "\n".join(positive_lines).strip(),
        "negative_prompt": "\n".join(negative_lines).strip(),
    }

    for key, raw_value in _RE_PARAM.findall(params_line):
        value = _unquote(raw_value.strip())
        parsed[key] = value
        size_match = _RE_IMAGE_SIZE.match(str(value))
        if key == "Size" and size_match:
            parsed["Size-1"] = int(size_match.group(1))
            parsed["Size-2"] = int(size_match.group(2))

    if not parsed["negative_prompt"]:
        parsed.pop("negative_prompt", None)
    if not parsed["positive_prompt"]:
        parsed.pop("positive_prompt", None)
    return parsed


def _extract_comfyui_parameters(text_items: dict[str, Any]) -> dict[str, Any]:
    prompt_graph = text_items.get("prompt")
    if not isinstance(prompt_graph, dict):
        return {}

    primary_sampler = _select_primary_sampler(prompt_graph)
    if not primary_sampler:
        return {}

    _, sampler_node = primary_sampler
    inputs = _node_inputs(sampler_node)
    parameters: dict[str, Any] = {}

    _maybe_set(
        parameters,
        "positive_prompt",
        _extract_prompt_text(prompt_graph, inputs.get("positive")),
    )
    _maybe_set(
        parameters,
        "negative_prompt",
        _extract_prompt_text(prompt_graph, inputs.get("negative")),
    )
    _maybe_set(parameters, "Steps", inputs.get("steps"))
    _maybe_set(parameters, "CFG scale", inputs.get("cfg"))
    _maybe_set(parameters, "Seed", inputs.get("noise_seed", inputs.get("seed")))
    _maybe_set(parameters, "Sampler", inputs.get("sampler_name"))
    _maybe_set(parameters, "Scheduler", inputs.get("scheduler"))
    _maybe_set(parameters, "Denoise", inputs.get("denoise"))

    image_size = _resolve_image_size(prompt_graph, inputs.get("latent_image"))
    if image_size:
        width, height = image_size
        parameters["Size"] = f"{width}x{height}"
        parameters["Size-1"] = width
        parameters["Size-2"] = height

    _maybe_set(
        parameters, "Model", _resolve_model_name(prompt_graph, inputs.get("model"))
    )
    return parameters


def _select_primary_sampler(
    prompt_graph: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    candidates: list[tuple[str, dict[str, Any]]] = []
    for node_id, node in prompt_graph.items():
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type", "")) in _COMFYUI_SAMPLER_TYPES:
            candidates.append((str(node_id), node))
    if not candidates:
        return None
    candidates.sort(key=lambda item: _node_sort_key(item[0]))
    return candidates[-1]


def _node_sort_key(node_id: str) -> tuple[int, int, str]:
    text = str(node_id)
    if text.isdigit():
        return (1, int(text), text)
    return (0, 0, text)


def _node_inputs(node: dict[str, Any]) -> dict[str, Any]:
    inputs = node.get("inputs")
    if isinstance(inputs, dict):
        return inputs
    return {}


def _get_prompt_node(
    prompt_graph: dict[str, Any], node_id: Any
) -> dict[str, Any] | None:
    key = str(node_id)
    node = prompt_graph.get(key)
    if isinstance(node, dict):
        return node
    return None


def _resolve_node_ref(ref: Any) -> str | None:
    if isinstance(ref, (list, tuple)) and ref:
        return str(ref[0])
    return None


def _extract_prompt_text(
    prompt_graph: dict[str, Any], ref: Any, visited: set[str] | None = None
) -> str:
    node_id = _resolve_node_ref(ref)
    if not node_id:
        return ""

    seen = visited or set()
    if node_id in seen:
        return ""
    seen = set(seen)
    seen.add(node_id)

    node = _get_prompt_node(prompt_graph, node_id)
    if not node:
        return ""

    class_type = str(node.get("class_type", ""))
    inputs = _node_inputs(node)
    if class_type == "CLIPTextEncode":
        return _normalize_prompt_text(inputs.get("text"))
    if class_type == "CLIPTextEncodeSDXL":
        return _join_labeled_prompt_fields(
            ("Global", inputs.get("text_g")),
            ("Local", inputs.get("text_l")),
        )
    if class_type == "CLIPTextEncodeSDXLRefiner":
        return _normalize_prompt_text(inputs.get("text"))
    if class_type == "CLIPTextEncodeFlux":
        return _join_labeled_prompt_fields(
            ("CLIP-L", inputs.get("clip_l")),
            ("T5XXL", inputs.get("t5xxl")),
        )
    if class_type.startswith("CLIPTextEncode"):
        return _extract_generic_clip_text(inputs)

    for key, value in inputs.items():
        normalized_key = str(key).lower()
        if (
            normalized_key in {"positive", "negative", "base"}
            or "conditioning" in normalized_key
        ):
            text = _extract_prompt_text(prompt_graph, value, seen)
            if text:
                return text
    return ""


def _extract_generic_clip_text(inputs: dict[str, Any]) -> str:
    preferred_fields = (
        ("Text", inputs.get("text")),
        ("Global", inputs.get("text_g")),
        ("Local", inputs.get("text_l")),
        ("CLIP-L", inputs.get("clip_l")),
        ("T5XXL", inputs.get("t5xxl")),
    )
    text = _join_labeled_prompt_fields(*preferred_fields)
    if text:
        return text

    discovered: list[tuple[str, Any]] = []
    for key, value in inputs.items():
        if isinstance(value, str) and value.strip():
            discovered.append((str(key).replace("_", " ").title(), value))
    return _join_labeled_prompt_fields(*discovered)


def _join_labeled_prompt_fields(*fields: tuple[str, Any]) -> str:
    normalized_fields: list[tuple[str, str]] = []
    for label, value in fields:
        text = _normalize_prompt_text(value)
        if text:
            normalized_fields.append((label, text))
    if not normalized_fields:
        return ""
    unique_values = {value for _, value in normalized_fields}
    if len(unique_values) == 1:
        return normalized_fields[0][1]
    return "\n".join(f"{label}: {value}" for label, value in normalized_fields)


def _normalize_prompt_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _resolve_image_size(
    prompt_graph: dict[str, Any], ref: Any, visited: set[str] | None = None
) -> tuple[int, int] | None:
    node_id = _resolve_node_ref(ref)
    if not node_id:
        return None

    seen = visited or set()
    if node_id in seen:
        return None
    seen = set(seen)
    seen.add(node_id)

    node = _get_prompt_node(prompt_graph, node_id)
    if not node:
        return None

    inputs = _node_inputs(node)
    width = _coerce_int(inputs.get("width"))
    height = _coerce_int(inputs.get("height"))
    if width and height:
        return (width, height)

    for key, value in inputs.items():
        normalized_key = str(key).lower()
        if (
            normalized_key in {"samples", "latent_image", "image", "pixels"}
            or "latent" in normalized_key
        ):
            resolved = _resolve_image_size(prompt_graph, value, seen)
            if resolved:
                return resolved
    return None


def _resolve_model_name(
    prompt_graph: dict[str, Any], ref: Any, visited: set[str] | None = None
) -> str:
    node_id = _resolve_node_ref(ref)
    if not node_id:
        return ""

    seen = visited or set()
    if node_id in seen:
        return ""
    seen = set(seen)
    seen.add(node_id)

    node = _get_prompt_node(prompt_graph, node_id)
    if not node:
        return ""

    inputs = _node_inputs(node)
    for key in ("ckpt_name", "model_name", "unet_name"):
        value = _normalize_prompt_text(inputs.get(key))
        if value:
            return value

    for key, value in inputs.items():
        normalized_key = str(key).lower()
        if normalized_key in {"model", "clip", "base_model"}:
            resolved = _resolve_model_name(prompt_graph, value, seen)
            if resolved:
                return resolved
    return ""


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _maybe_set(target: dict[str, Any], key: str, value: Any) -> None:
    if value not in (None, ""):
        target[key] = value


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / 1024 / 1024:.0f} MiB"


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except Exception:
            return value[1:-1]
    return value
