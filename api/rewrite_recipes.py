"""
F53 rewrite recipe API handlers.

Admin-only workflow:
- Recipe CRUD
- Dry-run preview with structured diff
- Guarded apply with rollback snapshot on failure
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from aiohttp import web

try:
    from ..services.access_control import require_admin_token, resolve_token_info
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
    from ..services.rewrite_recipes import (
        RecipeApplyError,
        RecipeValidationError,
        RewriteConstraints,
        RewriteOperation,
        RewriteRecipe,
        dry_run_recipe,
        guarded_apply_recipe,
        rewrite_recipe_store,
    )
    from ..services.tenant_context import TenantBoundaryError, request_tenant_scope
except ImportError:
    from services.access_control import (  # type: ignore
        require_admin_token,
        resolve_token_info,
    )
    from services.endpoint_manifest import (  # type: ignore
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
    from services.rewrite_recipes import (  # type: ignore
        RecipeApplyError,
        RecipeValidationError,
        RewriteConstraints,
        RewriteOperation,
        RewriteRecipe,
        dry_run_recipe,
        guarded_apply_recipe,
        rewrite_recipe_store,
    )
    from services.tenant_context import (  # type: ignore
        TenantBoundaryError,
        request_tenant_scope,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.rewrite_recipes")


def _json(data: Dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _require_admin(request: web.Request) -> web.Response | None:
    allowed, error = require_admin_token(request)
    if not allowed:
        return _json({"ok": False, "error": error or "unauthorized"}, 403)
    return None


def _build_recipe_from_payload(
    payload: Dict[str, Any], existing: RewriteRecipe | None = None
) -> RewriteRecipe:
    if existing is None:
        return RewriteRecipe.new(
            name=payload.get("name") or "",
            prompt_template=payload.get("prompt_template") or "",
            description=payload.get("description") or "",
            tags=payload.get("tags") or [],
            operations=payload.get("operations") or [],
            constraints=payload.get("constraints") or {},
            tenant_id=payload.get("tenant_id") or "default",
        )

    if "name" in payload:
        existing.name = payload["name"] or ""
    if "prompt_template" in payload:
        existing.prompt_template = payload.get("prompt_template") or ""
    if "description" in payload:
        existing.description = payload.get("description") or ""
    if "tags" in payload:
        existing.tags = payload.get("tags") or []
    if "operations" in payload:
        existing.operations = [
            RewriteOperation.from_dict(item)
            for item in (payload.get("operations") or [])
        ]
    if "constraints" in payload:
        existing.constraints = RewriteConstraints.from_dict(
            payload.get("constraints") or {}
        )
    existing.updated_at = time.time()
    existing.validate()
    return existing


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="List rewrite recipes",
    description="List workflow rewrite recipes.",
    audit="rewrite_recipes.list",
    plane=RoutePlane.ADMIN,
)
async def rewrite_recipes_list_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    tag = request.query.get("tag")
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request,
            token_info=token_info,
            allow_default_when_missing=True,
        ) as tenant:
            recipes = rewrite_recipe_store.list_recipes(
                tag=tag,
                tenant_id=tenant.tenant_id,
            )
            return _json({"ok": True, "recipes": [item.to_dict() for item in recipes]})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="Get rewrite recipe",
    description="Get a single workflow rewrite recipe.",
    audit="rewrite_recipes.get",
    plane=RoutePlane.ADMIN,
)
async def rewrite_recipe_get_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    recipe_id = request.match_info.get("recipe_id")
    if not recipe_id:
        return _json({"ok": False, "error": "missing_id"}, 400)
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request,
            token_info=token_info,
            allow_default_when_missing=True,
        ) as tenant:
            recipe = rewrite_recipe_store.get_recipe(
                recipe_id, tenant_id=tenant.tenant_id
            )
            if recipe is None:
                return _json({"ok": False, "error": "not_found"}, 404)
            return _json({"ok": True, "recipe": recipe.to_dict()})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Create rewrite recipe",
    description="Create a workflow rewrite recipe.",
    audit="rewrite_recipes.create",
    plane=RoutePlane.ADMIN,
)
async def rewrite_recipe_create_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    try:
        payload = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    if not isinstance(payload, dict):
        return _json({"ok": False, "error": "invalid_payload"}, 400)

    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request,
            token_info=token_info,
            allow_default_when_missing=True,
        ) as tenant:
            payload["tenant_id"] = tenant.tenant_id
            recipe = _build_recipe_from_payload(payload)
            rewrite_recipe_store.save_recipe(recipe)
            return _json({"ok": True, "recipe": recipe.to_dict()}, 201)
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
    except RecipeValidationError as exc:
        return _json({"ok": False, "error": exc.code, "detail": exc.detail}, 400)
    except Exception as exc:
        logger.exception("Failed to create rewrite recipe")
        return _json({"ok": False, "error": "internal_error", "detail": str(exc)}, 500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Update rewrite recipe",
    description="Update an existing workflow rewrite recipe.",
    audit="rewrite_recipes.update",
    plane=RoutePlane.ADMIN,
)
async def rewrite_recipe_update_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    recipe_id = request.match_info.get("recipe_id")
    if not recipe_id:
        return _json({"ok": False, "error": "missing_id"}, 400)
    try:
        payload = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    if not isinstance(payload, dict):
        return _json({"ok": False, "error": "invalid_payload"}, 400)

    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request,
            token_info=token_info,
            allow_default_when_missing=True,
        ) as tenant:
            recipe = rewrite_recipe_store.get_recipe(
                recipe_id, tenant_id=tenant.tenant_id
            )
            if recipe is None:
                return _json({"ok": False, "error": "not_found"}, 404)
            updated = _build_recipe_from_payload(payload, existing=recipe)
            rewrite_recipe_store.save_recipe(updated)
            return _json({"ok": True, "recipe": updated.to_dict()})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
    except RecipeValidationError as exc:
        return _json({"ok": False, "error": exc.code, "detail": exc.detail}, 400)
    except Exception as exc:
        logger.exception("Failed to update rewrite recipe")
        return _json({"ok": False, "error": "internal_error", "detail": str(exc)}, 500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Delete rewrite recipe",
    description="Delete a workflow rewrite recipe.",
    audit="rewrite_recipes.delete",
    plane=RoutePlane.ADMIN,
)
async def rewrite_recipe_delete_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    recipe_id = request.match_info.get("recipe_id")
    if not recipe_id:
        return _json({"ok": False, "error": "missing_id"}, 400)
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request,
            token_info=token_info,
            allow_default_when_missing=True,
        ) as tenant:
            if not rewrite_recipe_store.delete_recipe(
                recipe_id, tenant_id=tenant.tenant_id
            ):
                return _json({"ok": False, "error": "not_found"}, 404)
            return _json({"ok": True})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.MEDIUM,
    summary="Dry-run rewrite recipe",
    description="Preview rewrite result and structured diff without applying changes.",
    audit="rewrite_recipes.dry_run",
    plane=RoutePlane.ADMIN,
)
async def rewrite_recipe_dry_run_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    recipe_id = request.match_info.get("recipe_id")
    if not recipe_id:
        return _json({"ok": False, "error": "missing_id"}, 400)
    try:
        payload = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    workflow = payload.get("workflow") if isinstance(payload, dict) else None
    inputs = payload.get("inputs", {}) if isinstance(payload, dict) else {}
    if not isinstance(workflow, dict):
        return _json({"ok": False, "error": "missing_workflow"}, 400)
    if not isinstance(inputs, dict):
        return _json({"ok": False, "error": "invalid_inputs"}, 400)

    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request,
            token_info=token_info,
            allow_default_when_missing=True,
        ) as tenant:
            recipe = rewrite_recipe_store.get_recipe(
                recipe_id, tenant_id=tenant.tenant_id
            )
            if recipe is None:
                return _json({"ok": False, "error": "not_found"}, 404)
            result = dry_run_recipe(recipe, workflow=workflow, inputs=inputs)
            return _json({"ok": True, **result})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
    except RecipeValidationError as exc:
        return _json({"ok": False, "error": exc.code, "detail": exc.detail}, 400)
    except Exception as exc:
        logger.exception("Rewrite dry-run failed")
        return _json({"ok": False, "error": "internal_error", "detail": str(exc)}, 500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Apply rewrite recipe",
    description="Guarded apply with validation and rollback snapshot on failure.",
    audit="rewrite_recipes.apply",
    plane=RoutePlane.ADMIN,
)
async def rewrite_recipe_apply_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    recipe_id = request.match_info.get("recipe_id")
    if not recipe_id:
        return _json({"ok": False, "error": "missing_id"}, 400)
    try:
        payload = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    workflow = payload.get("workflow") if isinstance(payload, dict) else None
    inputs = payload.get("inputs", {}) if isinstance(payload, dict) else {}
    confirm = bool(payload.get("confirm")) if isinstance(payload, dict) else False
    if not isinstance(workflow, dict):
        return _json({"ok": False, "error": "missing_workflow"}, 400)
    if not isinstance(inputs, dict):
        return _json({"ok": False, "error": "invalid_inputs"}, 400)

    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request,
            token_info=token_info,
            allow_default_when_missing=True,
        ) as tenant:
            recipe = rewrite_recipe_store.get_recipe(
                recipe_id, tenant_id=tenant.tenant_id
            )
            if recipe is None:
                return _json({"ok": False, "error": "not_found"}, 404)
            result = guarded_apply_recipe(
                recipe,
                workflow=workflow,
                inputs=inputs,
                confirm=confirm,
            )
            return _json({"ok": True, **result})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
    except RecipeApplyError as exc:
        return _json(
            {
                "ok": False,
                "error": exc.code,
                "detail": exc.detail,
                "rollback_snapshot": exc.rollback_snapshot,
            },
            400,
        )
    except Exception as exc:
        logger.exception("Rewrite apply failed")
        return _json({"ok": False, "error": "internal_error", "detail": str(exc)}, 500)
