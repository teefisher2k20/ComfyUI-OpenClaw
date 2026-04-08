"""
PNG Info metadata parsing service (R168).
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

MAX_IMAGE_B64_LEN = 20 * 1024 * 1024  # 20MB base64 payload ceiling
_RE_PARAM = re.compile(r'\s*(\w[\w \-/]+):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)')
_RE_IMAGE_SIZE = re.compile(r"^(\d+)x(\d+)$")
_COMFYUI_KEYS = ("prompt", "workflow")
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
        info = "ComfyUI metadata detected."
        parameters = {}
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

    if len(image_b64) > MAX_IMAGE_B64_LEN:
        raise PngInfoError(
            "image_b64_too_large",
            f"image_b64 exceeds {MAX_IMAGE_B64_LEN // 1024 // 1024}MB",
        )

    value = image_b64.strip()
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    value = re.sub(r"\s+", "", value)
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


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except Exception:
            return value[1:-1]
    return value
