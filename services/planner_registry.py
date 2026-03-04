import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .safe_io import safe_read_json, safe_read_text
from .state_dir import get_state_dir

try:
    from ..models.schemas import GenerationParams, Profile
except ImportError:
    from models.schemas import GenerationParams, Profile

logger = logging.getLogger("ComfyUI-OpenClaw.services.planner_registry")

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PACK_ROOT = os.path.dirname(MODULE_DIR)
PACKAGE_PLANNER_ROOT = os.path.join(PACK_ROOT, "data", "planner")
STATE_PLANNER_SUBDIR = "planner"
PROFILES_FILE = "profiles.json"
PROMPT_FILE = "system_prompt.txt"
MAX_PROFILES_BYTES = 256 * 1024
MAX_PROMPT_BYTES = 64 * 1024
ALLOWED_PROMPT_PLACEHOLDERS = {
    "profile_id",
    "profile_label",
    "profile_description",
    "prompt_guidance",
    "defaults_json",
}
PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")

_EMBEDDED_FALLBACK_RAW = {
    "version": 1,
    "default_profile": "SDXL-v1",
    "profiles": [
        {
            "id": "SDXL-v1",
            "version": "1.0",
            "label": "SDXL 1.0 Base",
            "description": "Standard SDXL profile",
            "prompt_guidance": (
                "Width/height should target SDXL-friendly resolutions such as 1024x1024. "
                "Keep CFG around 7.0 and steps around 20-30 unless requirements strongly "
                "justify a deviation."
            ),
            "defaults": {
                "width": 1024,
                "height": 1024,
                "steps": 24,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
            },
        },
        {
            "id": "Flux-Dev",
            "version": "1.0",
            "label": "Flux Dev",
            "description": "Flux Dev profile (high steps, lower cfg)",
            "prompt_guidance": (
                "Flux variants generally prefer lower CFG than SDXL. Keep CFG around 1.0-4.0 "
                "and use moderately higher steps only when needed."
            ),
            "defaults": {
                "width": 1024,
                "height": 1024,
                "steps": 28,
                "cfg": 3.5,
                "sampler_name": "euler",
                "scheduler": "normal",
            },
        },
    ],
}

_EMBEDDED_FALLBACK_PROMPT = """You are an expert stable diffusion prompt engineer.
Your goal is to generate a detailed JSON plan for an image generation job based on the user's requirements.

Output strict JSON only. No markdown fences.
Expected JSON structure:
{
  "positive_prompt": "string",
  "negative_prompt": "string",
  "params": {
    "width": int,
    "height": int,
    "steps": int,
    "cfg": float,
    "sampler_name": "euler" | "dpmpp_2m" | "...",
    "scheduler": "normal" | "karras" | "..."
  }
}

Constraint Guidelines for {{profile_id}} ({{profile_label}}):
- {{profile_description}}
- {{prompt_guidance}}
- Preferred defaults JSON: {{defaults_json}}

Never return commentary outside the JSON object.
"""


@dataclass
class PlannerRegistryState:
    profiles: Dict[str, Profile]
    default_profile: str
    prompt_template: str
    profile_source: str
    prompt_source: str
    last_profile_error: Optional[str] = None
    last_prompt_error: Optional[str] = None


def _profile_from_entry(entry: Dict[str, Any]) -> Profile:
    required = ("id", "version", "label")
    missing = [
        key
        for key in required
        if not isinstance(entry.get(key), str) or not entry.get(key).strip()
    ]
    if missing:
        raise ValueError(f"planner profile missing required string fields: {missing}")
    defaults = entry.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise ValueError(
            f"planner profile '{entry.get('id')}' defaults must be an object"
        )
    validated_defaults = GenerationParams.from_dict(defaults).dict()
    prompt_guidance = entry.get("prompt_guidance", "")
    if prompt_guidance is None:
        prompt_guidance = ""
    if not isinstance(prompt_guidance, str):
        raise ValueError(
            f"planner profile '{entry.get('id')}' prompt_guidance must be a string"
        )
    description = entry.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError(
            f"planner profile '{entry.get('id')}' description must be a string or null"
        )
    return Profile(
        id=entry["id"].strip(),
        version=entry["version"].strip(),
        label=entry["label"].strip(),
        description=description.strip() if isinstance(description, str) else None,
        model_config_data={
            "prompt_guidance": prompt_guidance.strip(),
            "defaults": validated_defaults,
        },
    )


def _parse_profiles_payload(payload: Dict[str, Any]) -> tuple[Dict[str, Profile], str]:
    if not isinstance(payload, dict):
        raise ValueError("planner profiles payload must be an object")
    if payload.get("version") != 1:
        raise ValueError(
            f"unsupported planner profiles version: {payload.get('version')}"
        )
    entries = payload.get("profiles")
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            "planner profiles payload must contain a non-empty profiles list"
        )
    profiles: Dict[str, Profile] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("planner profile entries must be objects")
        profile = _profile_from_entry(entry)
        if profile.id in profiles:
            raise ValueError(f"duplicate planner profile id: {profile.id}")
        profiles[profile.id] = profile
    default_profile = payload.get("default_profile")
    if not isinstance(default_profile, str) or default_profile not in profiles:
        raise ValueError(
            "default_profile must reference an existing planner profile id"
        )
    return profiles, default_profile


def _validate_prompt_template(template: str) -> str:
    placeholders = set(PLACEHOLDER_RE.findall(template))
    unknown = sorted(placeholders - ALLOWED_PROMPT_PLACEHOLDERS)
    if unknown:
        raise ValueError(f"unsupported planner prompt placeholders: {unknown}")
    return template


class PlannerRegistry:
    def __init__(
        self,
        *,
        package_root: str = PACKAGE_PLANNER_ROOT,
        state_root: Optional[str] = None,
    ) -> None:
        self.package_root = package_root
        self.state_root = state_root or os.path.join(
            get_state_dir(), STATE_PLANNER_SUBDIR
        )
        self._state: Optional[PlannerRegistryState] = None
        self._watched_mtimes: Dict[str, Optional[float]] = {}

    def _file_mtime(self, path: str) -> Optional[float]:
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _current_watch_map(self) -> Dict[str, Optional[float]]:
        return {
            "package_profiles": self._file_mtime(
                os.path.join(self.package_root, PROFILES_FILE)
            ),
            "state_profiles": self._file_mtime(
                os.path.join(self.state_root, PROFILES_FILE)
            ),
            "package_prompt": self._file_mtime(
                os.path.join(self.package_root, PROMPT_FILE)
            ),
            "state_prompt": self._file_mtime(
                os.path.join(self.state_root, PROMPT_FILE)
            ),
        }

    def _load_profile_source(self) -> tuple[Dict[str, Profile], str, Optional[str]]:
        candidates = [
            (self.state_root, PROFILES_FILE, "state"),
            (self.package_root, PROFILES_FILE, "package"),
        ]
        last_error = None
        for root, rel_path, source_name in candidates:
            try:
                payload = safe_read_json(root, rel_path, max_bytes=MAX_PROFILES_BYTES)
                profiles, default_profile = _parse_profiles_payload(payload)
                return profiles, default_profile, source_name, last_error
            except FileNotFoundError:
                continue
            except Exception as exc:
                last_error = f"{source_name}:{type(exc).__name__}:{exc}"
                logger.warning(
                    "Planner profile source %s rejected: %s", source_name, exc
                )
        profiles, default_profile = _parse_profiles_payload(_EMBEDDED_FALLBACK_RAW)
        return profiles, default_profile, "embedded-fallback", last_error or "no_file"

    def _load_prompt_source(self) -> tuple[str, str, Optional[str]]:
        candidates = [
            (self.state_root, PROMPT_FILE, "state"),
            (self.package_root, PROMPT_FILE, "package"),
        ]
        last_error = None
        for root, rel_path, source_name in candidates:
            try:
                template = safe_read_text(root, rel_path, max_bytes=MAX_PROMPT_BYTES)
                return _validate_prompt_template(template), source_name, last_error
            except FileNotFoundError:
                continue
            except Exception as exc:
                last_error = f"{source_name}:{type(exc).__name__}:{exc}"
                logger.warning(
                    "Planner prompt source %s rejected: %s", source_name, exc
                )
        return (
            _validate_prompt_template(_EMBEDDED_FALLBACK_PROMPT),
            "embedded-fallback",
            last_error,
        )

    def _reload(self) -> None:
        profiles, default_profile, profile_source, profile_error = (
            self._load_profile_source()
        )
        prompt_template, prompt_source, prompt_error = self._load_prompt_source()
        self._state = PlannerRegistryState(
            profiles=profiles,
            default_profile=default_profile,
            prompt_template=prompt_template,
            profile_source=profile_source,
            prompt_source=prompt_source,
            last_profile_error=profile_error,
            last_prompt_error=prompt_error,
        )
        self._watched_mtimes = self._current_watch_map()

    def _ensure_loaded(self) -> PlannerRegistryState:
        current = self._current_watch_map()
        if self._state is None or current != self._watched_mtimes:
            self._reload()
        assert self._state is not None
        return self._state

    def list_profiles(self) -> list[Profile]:
        state = self._ensure_loaded()
        return [state.profiles[key] for key in sorted(state.profiles.keys())]

    def get_profile(self, profile_id: str) -> Optional[Profile]:
        state = self._ensure_loaded()
        return state.profiles.get(profile_id)

    def get_default_profile_id(self) -> str:
        return self._ensure_loaded().default_profile

    def render_system_prompt(self, profile_id: str) -> str:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError(f"Unknown profile: {profile_id}")
        state = self._ensure_loaded()
        metadata = profile.model_config_data or {}
        defaults = GenerationParams.from_dict(metadata.get("defaults", {})).dict()
        replacements = {
            "profile_id": profile.id,
            "profile_label": profile.label,
            "profile_description": profile.description or "No description provided.",
            "prompt_guidance": metadata.get("prompt_guidance")
            or "Use stable defaults for this profile.",
            "defaults_json": json.dumps(defaults, ensure_ascii=False, sort_keys=True),
        }
        rendered = state.prompt_template
        for key, value in replacements.items():
            rendered = re.sub(
                r"{{\s*" + re.escape(key) + r"\s*}}", str(value), rendered
            )
        return rendered

    def get_debug_info(self) -> Dict[str, Any]:
        state = self._ensure_loaded()
        return {
            "package_root": self.package_root,
            "state_root": self.state_root,
            "profile_source": state.profile_source,
            "prompt_source": state.prompt_source,
            "default_profile": state.default_profile,
            "profile_ids": [profile.id for profile in self.list_profiles()],
            "last_profile_error": state.last_profile_error,
            "last_prompt_error": state.last_prompt_error,
        }


_REGISTRY: Optional[PlannerRegistry] = None


def get_planner_registry() -> PlannerRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PlannerRegistry()
    return _REGISTRY
