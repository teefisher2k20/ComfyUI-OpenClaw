"""
F54 model manager API handlers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from aiohttp import web

try:
    from ..services.access_control import require_admin_token, resolve_token_info
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
    from ..services.model_manager import ModelManagerError, model_manager
    from ..services.tenant_context import TenantBoundaryError, request_tenant_scope
except ImportError:  # pragma: no cover
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
    from services.model_manager import ModelManagerError, model_manager  # type: ignore
    from services.tenant_context import (  # type: ignore
        TenantBoundaryError,
        request_tenant_scope,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.model_manager")


def _json(data: Dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _require_admin(request: web.Request) -> Optional[web.Response]:
    ok, error = require_admin_token(request)
    if ok:
        return None
    return _json({"ok": False, "error": error or "unauthorized"}, 403)


def _parse_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def _parse_optional_bool(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="Search models",
    description="Search normalized model entries across managed installs and catalog sources.",
    audit="models.search",
    plane=RoutePlane.ADMIN,
)
async def model_search_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            result = model_manager.search_models(
                query=request.query.get("q", ""),
                source=request.query.get("source", ""),
                model_type=request.query.get("model_type", ""),
                installed=_parse_optional_bool(request.query.get("installed")),
                limit=_parse_int(request.query.get("limit"), 50, 1, 200),
                offset=_parse_int(request.query.get("offset"), 0, 0, 10_000),
                tenant_id=tenant.tenant_id,
            )
            return _json({"ok": True, **result})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Create model download task",
    description="Create a managed model download task with progress/cancel lifecycle.",
    audit="models.download.create",
    plane=RoutePlane.ADMIN,
)
async def model_download_create_handler(request: web.Request) -> web.Response:
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
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            task = model_manager.create_download_task(
                model_id=payload.get("model_id") or payload.get("id") or "",
                name=payload.get("name") or "",
                model_type=payload.get("model_type") or "",
                source=payload.get("source") or "",
                source_label=payload.get("source_label") or "",
                download_url=payload.get("download_url") or "",
                expected_sha256=payload.get("expected_sha256") or "",
                provenance=payload.get("provenance") or {},
                destination_subdir=payload.get("destination_subdir"),
                filename=payload.get("filename"),
                tenant_id=tenant.tenant_id,
            )
            return _json({"ok": True, "task": task}, 201)
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
    except ModelManagerError as exc:
        return _json({"ok": False, "error": exc.code, "detail": exc.detail}, exc.status)
    except Exception as exc:
        logger.exception("Failed to create model download task")
        return _json({"ok": False, "error": "internal_error", "detail": str(exc)}, 500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="List model download tasks",
    description="List model download task states.",
    audit="models.download.list",
    plane=RoutePlane.ADMIN,
)
async def model_download_list_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            result = model_manager.list_download_tasks(
                tenant_id=tenant.tenant_id,
                state=request.query.get("state", ""),
                limit=_parse_int(request.query.get("limit"), 100, 1, 200),
                offset=_parse_int(request.query.get("offset"), 0, 0, 10_000),
            )
            return _json({"ok": True, **result})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="Get model download task",
    description="Get one model download task by task id.",
    audit="models.download.get",
    plane=RoutePlane.ADMIN,
)
async def model_download_get_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    task_id = request.match_info.get("task_id")
    if not task_id:
        return _json({"ok": False, "error": "missing_task_id"}, 400)
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            task = model_manager.get_download_task(task_id, tenant_id=tenant.tenant_id)
            return _json({"ok": True, "task": task})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
    except ModelManagerError as exc:
        return _json({"ok": False, "error": exc.code, "detail": exc.detail}, exc.status)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Cancel model download task",
    description="Cancel a queued/running model download task.",
    audit="models.download.cancel",
    plane=RoutePlane.ADMIN,
)
async def model_download_cancel_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    task_id = request.match_info.get("task_id")
    if not task_id:
        return _json({"ok": False, "error": "missing_task_id"}, 400)
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            task = model_manager.cancel_download_task(
                task_id, tenant_id=tenant.tenant_id
            )
            return _json({"ok": True, "task": task})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
    except ModelManagerError as exc:
        return _json({"ok": False, "error": exc.code, "detail": exc.detail}, exc.status)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.HIGH,
    summary="Import downloaded model",
    description="Activate/import a completed model download with policy checks.",
    audit="models.import",
    plane=RoutePlane.ADMIN,
)
async def model_import_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    try:
        payload = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    if not isinstance(payload, dict):
        return _json({"ok": False, "error": "invalid_payload"}, 400)
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        return _json({"ok": False, "error": "missing_task_id"}, 400)
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            rec = model_manager.import_downloaded_model(
                task_id=task_id,
                tenant_id=tenant.tenant_id,
                destination_subdir=payload.get("destination_subdir"),
                filename=payload.get("filename"),
                tags=(
                    payload.get("tags")
                    if isinstance(payload.get("tags"), list)
                    else None
                ),
            )
            return _json({"ok": True, "installation": rec})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
    except ModelManagerError as exc:
        return _json({"ok": False, "error": exc.code, "detail": exc.detail}, exc.status)
    except Exception as exc:
        logger.exception("Failed to import model download")
        return _json({"ok": False, "error": "internal_error", "detail": str(exc)}, 500)


@endpoint_metadata(
    auth=AuthTier.ADMIN,
    risk=RiskTier.LOW,
    summary="List installed models",
    description="List managed model installations.",
    audit="models.installations.list",
    plane=RoutePlane.ADMIN,
)
async def model_installations_list_handler(request: web.Request) -> web.Response:
    deny = _require_admin(request)
    if deny:
        return deny
    token_info = resolve_token_info(request)
    try:
        with request_tenant_scope(
            request=request, token_info=token_info, allow_default_when_missing=True
        ) as tenant:
            result = model_manager.list_installations(
                tenant_id=tenant.tenant_id,
                model_type=request.query.get("model_type", ""),
                limit=_parse_int(request.query.get("limit"), 100, 1, 200),
                offset=_parse_int(request.query.get("offset"), 0, 0, 10_000),
            )
            return _json({"ok": True, **result})
    except TenantBoundaryError as exc:
        return _json({"ok": False, "error": exc.code, "detail": str(exc)}, 403)
