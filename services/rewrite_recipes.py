"""
F53: Workflow rewrite recipe service.

Provides:
- Local recipe library CRUD storage.
- Dry-run rewrite preview with structured diff output.
- Guarded apply flow with validation + rollback snapshot on failure.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .execution_budgets import BudgetExceededError, check_render_size
from .state_dir import get_state_dir
from .tenant_context import (
    DEFAULT_TENANT_ID,
    is_multi_tenant_enabled,
    normalize_tenant_id,
)

try:
    from ..models.schemas import GenerationParams, MAX_INPUT_STRING_LENGTH
except Exception:  # pragma: no cover
    from models.schemas import GenerationParams, MAX_INPUT_STRING_LENGTH  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.services.rewrite_recipes")

STATE_SUBDIR = "rewrite_recipes"
MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 500
MAX_TAG_COUNT = 24
MAX_TAG_LENGTH = 48
MAX_OPERATIONS = 128
MAX_DIFF_ENTRIES = 200
MAX_TEMPLATE_STRING_LENGTH = 8_192

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*}}")
_FULL_PLACEHOLDER_RE = re.compile(r"^\{\{\s*([a-zA-Z0-9_]+)\s*}}$")


class RecipeValidationError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class RecipeApplyError(ValueError):
    def __init__(self, code: str, detail: str, rollback_snapshot: Dict[str, Any]):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.rollback_snapshot = rollback_snapshot


@dataclass
class RewriteOperation:
    path: str
    value: Any

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "RewriteOperation":
        if not isinstance(data, dict):
            raise RecipeValidationError("validation_error", "Operation must be an object")
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            raise RecipeValidationError(
                "validation_error", "Operation path must be a non-empty string"
            )
        return RewriteOperation(path=path.strip(), value=data.get("value"))

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "value": self.value}


@dataclass
class RewriteConstraints:
    required_inputs: List[str] = field(default_factory=list)
    allowed_inputs: List[str] = field(default_factory=list)
    max_string_length: int = MAX_INPUT_STRING_LENGTH

    @staticmethod
    def from_dict(data: Dict[str, Any] | None) -> "RewriteConstraints":
        if data is None:
            return RewriteConstraints()
        if not isinstance(data, dict):
            raise RecipeValidationError("validation_error", "constraints must be an object")
        required = data.get("required_inputs", [])
        allowed = data.get("allowed_inputs", [])
        max_len = data.get("max_string_length", MAX_INPUT_STRING_LENGTH)
        if not isinstance(required, list) or any(
            not isinstance(item, str) or not item.strip() for item in required
        ):
            raise RecipeValidationError(
                "validation_error", "constraints.required_inputs must be a string list"
            )
        if not isinstance(allowed, list) or any(
            not isinstance(item, str) or not item.strip() for item in allowed
        ):
            raise RecipeValidationError(
                "validation_error", "constraints.allowed_inputs must be a string list"
            )
        try:
            max_len = int(max_len)
        except Exception:
            raise RecipeValidationError(
                "validation_error", "constraints.max_string_length must be an integer"
            )
        if max_len < 1 or max_len > MAX_INPUT_STRING_LENGTH:
            raise RecipeValidationError(
                "validation_error",
                (
                    "constraints.max_string_length must be between 1 and "
                    f"{MAX_INPUT_STRING_LENGTH}"
                ),
            )
        return RewriteConstraints(
            required_inputs=sorted({item.strip() for item in required}),
            allowed_inputs=sorted({item.strip() for item in allowed}),
            max_string_length=max_len,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required_inputs": list(self.required_inputs),
            "allowed_inputs": list(self.allowed_inputs),
            "max_string_length": self.max_string_length,
        }


@dataclass
class RewriteRecipe:
    id: str
    name: str
    prompt_template: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    operations: List[RewriteOperation] = field(default_factory=list)
    constraints: RewriteConstraints = field(default_factory=RewriteConstraints)
    tenant_id: str = DEFAULT_TENANT_ID
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        *,
        name: str,
        prompt_template: str = "",
        description: str = "",
        tags: Optional[List[str]] = None,
        operations: Optional[List[Dict[str, Any]]] = None,
        constraints: Optional[Dict[str, Any]] = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> "RewriteRecipe":
        op_objects = [RewriteOperation.from_dict(item) for item in (operations or [])]
        recipe = cls(
            id=str(uuid.uuid4()),
            name=name,
            prompt_template=prompt_template or "",
            description=description or "",
            tags=_normalize_tags(tags or []),
            operations=op_objects,
            constraints=RewriteConstraints.from_dict(constraints),
            tenant_id=normalize_tenant_id(tenant_id, field_name="tenant_id"),
        )
        recipe.validate()
        return recipe

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "RewriteRecipe":
        if not isinstance(data, dict):
            raise RecipeValidationError("validation_error", "Recipe file must be an object")
        operations = [
            RewriteOperation.from_dict(item) for item in data.get("operations", [])
        ]
        recipe = RewriteRecipe(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            prompt_template=str(data.get("prompt_template") or ""),
            description=str(data.get("description") or ""),
            tags=_normalize_tags(data.get("tags", [])),
            operations=operations,
            constraints=RewriteConstraints.from_dict(data.get("constraints")),
            tenant_id=normalize_tenant_id(
                data.get("tenant_id") or DEFAULT_TENANT_ID, field_name="tenant_id"
            ),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )
        recipe.validate()
        return recipe

    def validate(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise RecipeValidationError("validation_error", "Recipe id must be a string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise RecipeValidationError("validation_error", "Recipe name is required")
        if len(self.name.strip()) > MAX_NAME_LENGTH:
            raise RecipeValidationError(
                "validation_error",
                f"Recipe name exceeds {MAX_NAME_LENGTH} characters",
            )
        if not isinstance(self.description, str):
            raise RecipeValidationError("validation_error", "description must be a string")
        if len(self.description) > MAX_DESCRIPTION_LENGTH:
            raise RecipeValidationError(
                "validation_error",
                f"description exceeds {MAX_DESCRIPTION_LENGTH} characters",
            )
        if not isinstance(self.prompt_template, str):
            raise RecipeValidationError(
                "validation_error", "prompt_template must be a string"
            )
        if len(self.prompt_template) > MAX_TEMPLATE_STRING_LENGTH:
            raise RecipeValidationError(
                "validation_error",
                f"prompt_template exceeds {MAX_TEMPLATE_STRING_LENGTH} characters",
            )
        self.tags = _normalize_tags(self.tags)
        if len(self.operations) > MAX_OPERATIONS:
            raise RecipeValidationError(
                "validation_error",
                f"operations exceeds limit ({MAX_OPERATIONS})",
            )
        if not self.operations and not self.prompt_template.strip():
            raise RecipeValidationError(
                "validation_error",
                "Recipe requires at least one operation or a prompt_template",
            )
        seen_paths = set()
        for op in self.operations:
            _parse_json_pointer(op.path)
            if op.path in seen_paths:
                raise RecipeValidationError(
                    "validation_error", f"Duplicate operation path: {op.path}"
                )
            seen_paths.add(op.path)
            _assert_json_serializable(op.value)
        self.constraints = RewriteConstraints.from_dict(self.constraints.to_dict())
        self.tenant_id = normalize_tenant_id(self.tenant_id, field_name="tenant_id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompt_template": self.prompt_template,
            "description": self.description,
            "tags": list(self.tags),
            "operations": [op.to_dict() for op in self.operations],
            "constraints": self.constraints.to_dict(),
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _normalize_tags(tags: List[Any]) -> List[str]:
    if not isinstance(tags, list):
        raise RecipeValidationError("validation_error", "tags must be a list")
    out: List[str] = []
    seen = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise RecipeValidationError("validation_error", "tags must be strings")
        clean = tag.strip().lower()
        if not clean:
            continue
        if len(clean) > MAX_TAG_LENGTH:
            raise RecipeValidationError(
                "validation_error",
                f"tag exceeds {MAX_TAG_LENGTH} characters: {clean}",
            )
        if clean in seen:
            continue
        out.append(clean)
        seen.add(clean)
    if len(out) > MAX_TAG_COUNT:
        raise RecipeValidationError(
            "validation_error",
            f"tags exceeds limit ({MAX_TAG_COUNT})",
        )
    return out


def _assert_json_serializable(value: Any) -> None:
    try:
        json.dumps(value, ensure_ascii=False)
    except Exception:
        raise RecipeValidationError("validation_error", "operation value is not JSON-serializable")


class RewriteRecipeStore:
    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            storage_dir = Path(get_state_dir()) / STATE_SUBDIR
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, recipe_id: str) -> Path:
        return self.storage_dir / f"{recipe_id}.json"

    def _resolve_tenant_id(self, tenant_id: Optional[str]) -> Optional[str]:
        if not is_multi_tenant_enabled():
            return None
        try:
            return normalize_tenant_id(tenant_id or DEFAULT_TENANT_ID)
        except Exception:
            return DEFAULT_TENANT_ID

    def _is_visible(self, recipe: RewriteRecipe, tenant_id: Optional[str]) -> bool:
        resolved = self._resolve_tenant_id(tenant_id)
        if resolved is None:
            return True
        return recipe.tenant_id == resolved

    def list_recipes(
        self,
        *,
        tag: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[RewriteRecipe]:
        results: List[RewriteRecipe] = []
        if not self.storage_dir.exists():
            return results
        for path in sorted(self.storage_dir.glob("*.json")):
            recipe = self._load_file(path)
            if recipe is None:
                continue
            if not self._is_visible(recipe, tenant_id):
                continue
            if tag and tag.strip().lower() not in recipe.tags:
                continue
            results.append(recipe)
        results.sort(key=lambda item: item.updated_at, reverse=True)
        return results

    def get_recipe(
        self, recipe_id: str, *, tenant_id: Optional[str] = None
    ) -> Optional[RewriteRecipe]:
        path = self._path_for(recipe_id)
        if not path.exists():
            return None
        recipe = self._load_file(path)
        if recipe is None:
            return None
        if not self._is_visible(recipe, tenant_id):
            return None
        return recipe

    def save_recipe(self, recipe: RewriteRecipe) -> bool:
        recipe.validate()
        try:
            recipe.tenant_id = normalize_tenant_id(recipe.tenant_id, field_name="tenant_id")
        except Exception:
            recipe.tenant_id = DEFAULT_TENANT_ID
        path = self._path_for(recipe.id)
        payload = json.dumps(recipe.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_write(path, payload)
        return True

    def delete_recipe(self, recipe_id: str, *, tenant_id: Optional[str] = None) -> bool:
        path = self._path_for(recipe_id)
        if not path.exists():
            return False
        recipe = self._load_file(path)
        if recipe is not None and not self._is_visible(recipe, tenant_id):
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def _load_file(self, path: Path) -> Optional[RewriteRecipe]:
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
            return RewriteRecipe.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to load rewrite recipe %s: %s", path.name, exc)
            return None


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{path.name}.tmp.", dir=str(path.parent), text=False
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def _parse_json_pointer(path: str) -> List[str]:
    if not isinstance(path, str) or not path.startswith("/") or len(path) < 2:
        raise RecipeValidationError(
            "validation_error",
            f"operation path must be RFC6901-style JSON pointer: {path!r}",
        )
    parts = path.split("/")[1:]
    decoded = [segment.replace("~1", "/").replace("~0", "~") for segment in parts]
    if any(part == "" for part in decoded):
        raise RecipeValidationError(
            "validation_error", f"operation path contains empty segment: {path!r}"
        )
    return decoded


def _parse_list_index(part: str, length: int, *, path: str) -> int:
    try:
        index = int(part)
    except Exception:
        raise RecipeValidationError(
            "validation_error", f"List index must be integer at {path!r}"
        )
    if index < 0 or index >= length:
        raise RecipeValidationError(
            "validation_error",
            f"List index out of bounds ({index}) at {path!r}",
        )
    return index


def _set_json_pointer(doc: Dict[str, Any], path: str, value: Any) -> None:
    parts = _parse_json_pointer(path)
    current: Any = doc
    traversed: List[str] = []
    for part in parts[:-1]:
        traversed.append(part)
        if isinstance(current, dict):
            if part not in current:
                raise RecipeValidationError(
                    "validation_error",
                    f"Path not found: /{'/'.join(traversed)}",
                )
            current = current[part]
            continue
        if isinstance(current, list):
            idx = _parse_list_index(part, len(current), path=path)
            current = current[idx]
            continue
        raise RecipeValidationError(
            "validation_error",
            f"Path not traversable at /{'/'.join(traversed)}",
        )

    leaf = parts[-1]
    if isinstance(current, dict):
        if leaf not in current:
            raise RecipeValidationError("validation_error", f"Path not found: {path}")
        current[leaf] = value
        return
    if isinstance(current, list):
        idx = _parse_list_index(leaf, len(current), path=path)
        current[idx] = value
        return
    raise RecipeValidationError("validation_error", f"Path not writable: {path}")


def _render_template_string(template: str, context: Dict[str, Any]) -> Any:
    full_match = _FULL_PLACEHOLDER_RE.match(template)
    if full_match:
        key = full_match.group(1)
        if key not in context:
            raise RecipeValidationError(
                "validation_error", f"Missing input for placeholder: {key}"
            )
        return context[key]

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise RecipeValidationError(
                "validation_error", f"Missing input for placeholder: {key}"
            )
        return str(context[key])

    return _PLACEHOLDER_RE.sub(_replace, template)


def _render_template_value(value: Any, context: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_template_string(value, context)
    if isinstance(value, list):
        return [_render_template_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_template_value(item, context) for key, item in value.items()}
    return value


def _validate_apply_inputs(
    inputs: Dict[str, Any], constraints: RewriteConstraints
) -> Dict[str, Any]:
    if not isinstance(inputs, dict):
        raise RecipeValidationError("validation_error", "inputs must be an object")

    for key in constraints.required_inputs:
        if key not in inputs:
            raise RecipeValidationError(
                "validation_error", f"Missing required input: {key}"
            )

    if constraints.allowed_inputs:
        unknown = sorted(key for key in inputs.keys() if key not in constraints.allowed_inputs)
        if unknown:
            raise RecipeValidationError(
                "validation_error",
                f"Unknown inputs not allowed by recipe constraints: {unknown}",
            )

    normalized = dict(inputs)
    for key, value in normalized.items():
        if isinstance(value, str) and len(value) > constraints.max_string_length:
            raise RecipeValidationError(
                "validation_error",
                f"Input '{key}' exceeds max_string_length ({constraints.max_string_length})",
            )

    # CRITICAL: Keep GenerationParams clamp in this apply path so F53 outputs
    # continue inheriting S3 bounds for width/height/steps/cfg.
    clamp_fields = {
        key: normalized[key]
        for key in ("width", "height", "steps", "cfg")
        if key in normalized
    }
    if clamp_fields:
        clamped = GenerationParams.from_dict(clamp_fields).dict()
        for key in clamp_fields.keys():
            normalized[key] = clamped[key]

    return normalized


def _diff_preview_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > 200:
            return value[:200] + "...(truncated)"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return {"type": "list", "length": len(value)}
    if isinstance(value, dict):
        return {"type": "dict", "keys": len(value)}
    return str(value)[:200]


def _collect_diff(
    before: Any,
    after: Any,
    *,
    path: str,
    output: List[Dict[str, Any]],
) -> None:
    if len(output) >= MAX_DIFF_ENTRIES:
        return
    if type(before) is not type(after):
        output.append(
            {
                "path": path,
                "change": "type_changed",
                "before": _diff_preview_value(before),
                "after": _diff_preview_value(after),
            }
        )
        return
    if isinstance(before, dict):
        keys = sorted(set(before.keys()) | set(after.keys()))
        for key in keys:
            if len(output) >= MAX_DIFF_ENTRIES:
                return
            child_path = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
            if key not in before:
                output.append(
                    {
                        "path": child_path,
                        "change": "added",
                        "before": None,
                        "after": _diff_preview_value(after[key]),
                    }
                )
                continue
            if key not in after:
                output.append(
                    {
                        "path": child_path,
                        "change": "removed",
                        "before": _diff_preview_value(before[key]),
                        "after": None,
                    }
                )
                continue
            _collect_diff(before[key], after[key], path=child_path, output=output)
        return
    if isinstance(before, list):
        max_len = max(len(before), len(after))
        for idx in range(max_len):
            if len(output) >= MAX_DIFF_ENTRIES:
                return
            child_path = f"{path}/{idx}"
            if idx >= len(before):
                output.append(
                    {
                        "path": child_path,
                        "change": "added",
                        "before": None,
                        "after": _diff_preview_value(after[idx]),
                    }
                )
                continue
            if idx >= len(after):
                output.append(
                    {
                        "path": child_path,
                        "change": "removed",
                        "before": _diff_preview_value(before[idx]),
                        "after": None,
                    }
                )
                continue
            _collect_diff(before[idx], after[idx], path=child_path, output=output)
        return
    if before != after:
        output.append(
            {
                "path": path,
                "change": "modified",
                "before": _diff_preview_value(before),
                "after": _diff_preview_value(after),
            }
        )


def build_structured_diff(
    before: Dict[str, Any], after: Dict[str, Any]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    _collect_diff(before, after, path="", output=out)
    return out[:MAX_DIFF_ENTRIES]


def dry_run_recipe(
    recipe: RewriteRecipe,
    *,
    workflow: Dict[str, Any],
    inputs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    recipe.validate()
    if not isinstance(workflow, dict):
        raise RecipeValidationError("validation_error", "workflow must be an object")

    before = copy.deepcopy(workflow)
    rendered = copy.deepcopy(workflow)
    safe_inputs = _validate_apply_inputs(inputs or {}, recipe.constraints)

    if recipe.prompt_template.strip():
        safe_inputs.setdefault(
            "rewrite_prompt",
            _render_template_string(recipe.prompt_template, safe_inputs),
        )

    for op in recipe.operations:
        value = _render_template_value(op.value, safe_inputs)
        _set_json_pointer(rendered, op.path, value)

    try:
        check_render_size(rendered, trace_id=f"rewrite_recipe:{recipe.id}")
    except BudgetExceededError as exc:
        raise RecipeValidationError("budget_exceeded", str(exc))

    diff = build_structured_diff(before, rendered)
    serialized = json.dumps(rendered, ensure_ascii=False, separators=(",", ":"))
    return {
        "recipe_id": recipe.id,
        "workflow": rendered,
        "diff": diff,
        "render": {
            "workflow_bytes": len(serialized.encode("utf-8")),
            "node_count_estimate": len(rendered),
            "diff_entries": len(diff),
        },
    }


def guarded_apply_recipe(
    recipe: RewriteRecipe,
    *,
    workflow: Dict[str, Any],
    inputs: Optional[Dict[str, Any]] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    rollback_snapshot = copy.deepcopy(workflow)
    if not confirm:
        raise RecipeApplyError(
            "apply_requires_confirm",
            "Set confirm=true to execute guarded apply.",
            rollback_snapshot,
        )
    try:
        dry_run = dry_run_recipe(recipe, workflow=workflow, inputs=inputs or {})
    except RecipeValidationError as exc:
        raise RecipeApplyError(exc.code, exc.detail, rollback_snapshot)
    except Exception as exc:
        raise RecipeApplyError("apply_failed", str(exc), rollback_snapshot)

    return {
        "recipe_id": recipe.id,
        "applied_workflow": dry_run["workflow"],
        "diff": dry_run["diff"],
        "render": dry_run["render"],
    }


rewrite_recipe_store = RewriteRecipeStore()
