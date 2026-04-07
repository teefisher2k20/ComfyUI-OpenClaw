"""
F10/F13/F46 — Bridge API Endpoints.
Sidecar-facing endpoints for job submission, delivery, and worker polling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any, Dict, Optional

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover (optional for unit tests)
    web = None  # type: ignore

try:
    from ..services.async_utils import run_in_thread
    from ..services.audit import emit_audit_event

    # CRITICAL: handshake verifier must be imported in package mode;
    # missing this causes NameError at runtime on /bridge/handshake.
    from ..services.bridge_handshake import verify_handshake
    from ..services.execution_budgets import BudgetExceededError
    from ..services.idempotency_store import IdempotencyStore
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.redaction import stable_redaction_tag
    from ..services.sidecar.auth import is_bridge_enabled, require_bridge_auth
    from ..services.sidecar.bridge_contract import (
        BRIDGE_ENDPOINTS,
        BridgeDeliveryRequest,
        BridgeHealthResponse,
        BridgeJobRequest,
        BridgeScope,
    )
    from ..services.trace import get_effective_trace_id
    from ..services.trace_store import trace_store
except ImportError:
    # Fallback for ComfyUI's non-package loader or ad-hoc imports.
    from services.async_utils import run_in_thread
    from services.audit import emit_audit_event
    from services.bridge_handshake import verify_handshake
    from services.execution_budgets import BudgetExceededError
    from services.idempotency_store import IdempotencyStore
    from services.rate_limit import build_rate_limit_response, check_rate_limit
    from services.redaction import stable_redaction_tag
    from services.sidecar.auth import is_bridge_enabled, require_bridge_auth
    from services.sidecar.bridge_contract import (
        BRIDGE_ENDPOINTS,
        BridgeDeliveryRequest,
        BridgeHealthResponse,
        BridgeJobRequest,
        BridgeScope,
    )
    from services.trace import get_effective_trace_id
    from services.trace_store import trace_store

# R98: Endpoint Metadata
if __package__ and "." in __package__:
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
else:
    from services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.bridge")

# Payload limits
MAX_INPUTS_SIZE = 64 * 1024  # 64KB JSON
MAX_TEXT_LENGTH = 8000  # 8K chars
MAX_FILES_COUNT = 10

# Track startup time for uptime
_startup_time = time.time()


def _bridge_sensitive_tag(value: Optional[str], *, label: str) -> str:
    return stable_redaction_tag(value, label=label)


class BridgeHandlers:
    """Handlers for bridge API endpoints."""

    def __init__(self, submit_service=None, delivery_router=None):
        """
        Args:
            submit_service: Service for job submission (injected)
            delivery_router: Router for delivery requests (injected)
        """
        self.submit_service = submit_service
        self.delivery_router = delivery_router
        # R22: Bounded Idempotency Store
        # S50: Durable Idempotency Backend
        self._idempotency_store = IdempotencyStore()

        # F46: Worker job queue (in-memory stub, production would use persistent store)
        self._worker_job_queue: list = []
        # F46: Worker result store
        self._worker_results: dict = {}
        # F46: Worker heartbeats
        self._worker_heartbeats: dict = {}

    def _bridge_token(self, device_id: Optional[str], scope: Optional[str] = None):
        scopes = {scope} if scope else set()
        return SimpleNamespace(
            token_id=f"bridge:{_bridge_sensitive_tag(device_id, label='device')}",
            role="bridge",
            scopes=scopes,
        )

    def _audit(
        self,
        *,
        request: web.Request,
        action: str,
        target: str,
        outcome: str,
        status_code: int,
        device_id: Optional[str] = None,
        scope: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        emit_audit_event(
            action=action,
            target=target,
            outcome=outcome,
            token_info=self._bridge_token(device_id, scope),
            status_code=status_code,
            details=details or {},
            request=request,
        )

    @endpoint_metadata(
        auth=AuthTier.PUBLIC,  # Guarded by is_bridge_enabled internally, effectively public if enabled
        risk=RiskTier.LOW,
        summary="Bridge health",
        description="Returns bridge health status.",
        plane=RoutePlane.USER,
    )
    async def health_handler(self, request: web.Request) -> web.Response:
        """
        GET /bridge/health
        Returns bridge health status. Safe, low-sensitivity endpoint.
        """
        if not is_bridge_enabled():
            return web.json_response({"error": "Bridge not enabled"}, status=403)

        # Get version from package
        try:
            from services.pack_info import get_pack_info

            pack = get_pack_info()
            version = pack.get("version", "unknown")
        except Exception:
            version = "unknown"

        response = BridgeHealthResponse(
            ok=True,
            version=version,
            uptime_sec=time.time() - _startup_time,
            job_queue_depth=0,  # TODO: Wire to actual queue
        )

        return web.json_response(
            {
                "ok": response.ok,
                "version": response.version,
                "uptime_sec": response.uptime_sec,
                "job_queue_depth": response.job_queue_depth,
            }
        )

    @endpoint_metadata(
        auth=AuthTier.PUBLIC,
        risk=RiskTier.LOW,
        summary="Bridge handshake",
        description="Negotiate protocol version.",
        plane=RoutePlane.USER,
    )
    async def handshake_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/handshake
        Negotiate protocol version compatibility.
        """
        try:
            data = await request.json()
            client_version = int(data.get("version", 0))
        except (ValueError, TypeError, Exception):
            return web.json_response({"error": "Invalid version format"}, status=400)

        ok, msg, meta = verify_handshake(client_version)

        status_code = 200 if ok else 409  # 409 Conflict for version mismatch

        return web.json_response(
            {
                "ok": ok,
                "message": msg,
                "metadata": meta,
            },
            status=status_code,
        )

    @endpoint_metadata(
        auth=AuthTier.BRIDGE,
        risk=RiskTier.HIGH,
        summary="Bridge submit",
        description="Submit a job via sidecar bridge.",
        audit="bridge.submit",
        plane=RoutePlane.INTERNAL,
    )
    async def submit_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/submit
        Submit a job via sidecar bridge.
        """
        # S62: Block webhook execution in public+split mode
        try:
            # CRITICAL: package-relative import must stay first in ComfyUI runtime.
            from ..services.surface_guard import check_surface
        except ImportError:
            from services.surface_guard import check_surface  # type: ignore
        blocked = check_surface("webhook_execute", request)
        if blocked:
            return blocked

        # Auth check
        is_valid, error_resp, device_id = require_bridge_auth(
            request, BridgeScope.JOB_SUBMIT
        )
        if not is_valid:
            self._audit(
                request=request,
                action="bridge.submit",
                target="bridge.submit",
                outcome="deny",
                status_code=getattr(error_resp, "status", 403),
                details={"reason": "auth_failed"},
            )
            return error_resp

        # Rate limit
        if not check_rate_limit(request, "bridge"):
            self._audit(
                request=request,
                action="bridge.submit",
                target="bridge.submit",
                outcome="deny",
                status_code=429,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"reason": "rate_limit"},
            )
            return build_rate_limit_response(
                request,
                "bridge",
                web_module=web,
                error="Rate limit exceeded",
                include_ok=False,
            )

        # Parse payload
        try:
            data = await request.json()
        except Exception:
            self._audit(
                request=request,
                action="bridge.submit",
                target="bridge.submit",
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"reason": "invalid_json"},
            )
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # R25: Trace context
        trace_id = get_effective_trace_id(request.headers, data)

        # Validate required fields
        template_id = data.get("template_id")
        inputs = data.get("inputs", {})
        idempotency_key = data.get("idempotency_key")

        if not template_id:
            self._audit(
                request=request,
                action="bridge.submit",
                target="bridge.submit",
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"reason": "missing_template_id"},
            )
            return web.json_response({"error": "template_id required"}, status=400)
        if not idempotency_key:
            self._audit(
                request=request,
                action="bridge.submit",
                target="bridge.submit",
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"reason": "missing_idempotency_key"},
            )
            return web.json_response({"error": "idempotency_key required"}, status=400)

        # Payload size check
        import json

        inputs_size = len(json.dumps(inputs))
        if inputs_size > MAX_INPUTS_SIZE:
            self._audit(
                request=request,
                action="bridge.submit",
                target="bridge.submit",
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"reason": "inputs_too_large", "size": inputs_size},
            )
            return web.json_response(
                {"error": f"inputs exceeds {MAX_INPUTS_SIZE // 1024}KB"}, status=400
            )

        # Idempotency check (S50 Durable)
        store_key = f"bridge:{idempotency_key}"
        is_dup, existing_pid = self._idempotency_store.check_and_record(
            store_key, ttl=86400
        )

        if is_dup:
            logger.info(f"Duplicate bridge submit suppressed: {store_key}")
            return web.json_response(
                {
                    "ok": True,
                    "deduped": True,
                    "prompt_id": existing_pid,
                    "trace_id": trace_id,
                    "status": "queued",
                    "message": "Duplicate request suppressed",
                }
            )

        # Build request object
        job_request = BridgeJobRequest(
            template_id=template_id,
            inputs=inputs,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            session_id=data.get("session_id"),
            device_id=device_id,
            delivery_target=data.get("delivery_target"),
            timeout_sec=data.get("timeout_sec", 300),
        )

        # Submit to job service
        try:
            if self.submit_service:
                result = await run_in_thread(self.submit_service.submit, job_request)
                prompt_id = result.get("prompt_id", "")
            else:
                # Fail-closed: No submit service wired
                logger.error(
                    "BridgeHandlers.submit_service not wired - cannot submit job"
                )
                return web.json_response(
                    {"error": "Bridge submit service not configured", "ok": False},
                    status=503,
                )

            response_data = {
                "ok": True,
                "prompt_id": prompt_id,
                "trace_id": trace_id,
                "status": "queued",
            }

            # R25: Record trace mapping + queued event
            try:
                if prompt_id:
                    trace_store.add_event(
                        prompt_id, trace_id, "queued", {"source": "bridge"}
                    )
            except Exception:
                pass

            # Update durable store with prompt_id
            if prompt_id:
                self._idempotency_store.update_prompt_id(store_key, prompt_id)

            self._audit(
                request=request,
                action="bridge.submit",
                target=template_id,
                outcome="allow",
                status_code=200,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"prompt_id": prompt_id, "trace_id": trace_id},
            )
            return web.json_response(response_data)

        except BudgetExceededError as e:
            logger.warning(f"Bridge submit denied by execution budget: {e}")
            self._audit(
                request=request,
                action="bridge.submit",
                target=template_id,
                outcome="deny",
                status_code=429,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"reason": "budget_exceeded", "error": str(e)},
            )
            return web.json_response(
                {"error": "budget_exceeded", "detail": str(e)},
                status=429,
                headers={"Retry-After": str(getattr(e, "retry_after", 1))},
            )
        except Exception as e:
            logger.exception("Bridge submit failed")
            self._audit(
                request=request,
                action="bridge.submit",
                target=template_id or "bridge.submit",
                outcome="error",
                status_code=500,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"error": str(e)},
            )
            return web.json_response({"error": "Internal server error"}, status=500)

    @endpoint_metadata(
        auth=AuthTier.BRIDGE,
        risk=RiskTier.HIGH,
        summary="Bridge deliver",
        description="Request outbound delivery via sidecar.",
        audit="bridge.deliver",
        plane=RoutePlane.INTERNAL,
    )
    async def deliver_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/deliver
        Request outbound delivery via sidecar.
        """
        # S62: Block callback egress in public+split mode
        try:
            # CRITICAL: package-relative import must stay first in ComfyUI runtime.
            from ..services.surface_guard import check_surface
        except ImportError:
            from services.surface_guard import check_surface  # type: ignore
        blocked = check_surface("callback_egress", request)
        if blocked:
            return blocked

        # Auth check
        is_valid, error_resp, device_id = require_bridge_auth(
            request, BridgeScope.DELIVERY
        )
        if not is_valid:
            self._audit(
                request=request,
                action="bridge.deliver",
                target="bridge.deliver",
                outcome="deny",
                status_code=getattr(error_resp, "status", 403),
                details={"reason": "auth_failed"},
            )
            return error_resp

        # Rate limit
        if not check_rate_limit(request, "bridge"):
            self._audit(
                request=request,
                action="bridge.deliver",
                target="bridge.deliver",
                outcome="deny",
                status_code=429,
                device_id=device_id,
                scope=BridgeScope.DELIVERY.value,
                details={"reason": "rate_limit"},
            )
            return build_rate_limit_response(
                request,
                "bridge",
                web_module=web,
                error="Rate limit exceeded",
                include_ok=False,
            )

        # Parse payload
        try:
            data = await request.json()
        except Exception:
            self._audit(
                request=request,
                action="bridge.deliver",
                target="bridge.deliver",
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.DELIVERY.value,
                details={"reason": "invalid_json"},
            )
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # R25: Trace context
        trace_id = get_effective_trace_id(request.headers, data)

        # Validate required fields
        target = data.get("target")
        text = data.get("text", "")
        idempotency_key = data.get("idempotency_key")
        files = data.get("files", [])

        if not target:
            self._audit(
                request=request,
                action="bridge.deliver",
                target="bridge.deliver",
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.DELIVERY.value,
                details={"reason": "missing_target"},
            )
            return web.json_response({"error": "target required"}, status=400)
        if not idempotency_key:
            self._audit(
                request=request,
                action="bridge.deliver",
                target=target or "bridge.deliver",
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.DELIVERY.value,
                details={"reason": "missing_idempotency_key"},
            )
            return web.json_response({"error": "idempotency_key required"}, status=400)

        # Payload size checks
        if len(text) > MAX_TEXT_LENGTH:
            self._audit(
                request=request,
                action="bridge.deliver",
                target=target,
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.DELIVERY.value,
                details={"reason": "text_too_large", "length": len(text)},
            )
            return web.json_response(
                {"error": f"text exceeds {MAX_TEXT_LENGTH} chars"}, status=400
            )
        if len(files) > MAX_FILES_COUNT:
            self._audit(
                request=request,
                action="bridge.deliver",
                target=target,
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.DELIVERY.value,
                details={"reason": "too_many_files", "count": len(files)},
            )
            return web.json_response(
                {"error": f"files exceeds {MAX_FILES_COUNT}"}, status=400
            )

        # Build request
        delivery_request = BridgeDeliveryRequest(
            target=target,
            text=text,
            idempotency_key=idempotency_key,
            files=files,
        )

        # Route to delivery adapter
        try:
            if self.delivery_router:
                success = await self.delivery_router.route(delivery_request)
            else:
                # Stub: No delivery router wired
                logger.warning("BridgeHandlers.delivery_router not wired")
                success = True

            self._audit(
                request=request,
                action="bridge.deliver",
                target=target,
                outcome="allow" if success else "error",
                status_code=200 if success else 500,
                device_id=device_id,
                scope=BridgeScope.DELIVERY.value,
                details={"trace_id": trace_id},
            )

            return web.json_response(
                {
                    "ok": success,
                    "status": "delivered" if success else "failed",
                }
            )

        except Exception as e:
            logger.exception("Bridge deliver failed")
            self._audit(
                request=request,
                action="bridge.deliver",
                target=target if "target" in locals() else "bridge.deliver",
                outcome="error",
                status_code=500,
                device_id=device_id,
                scope=BridgeScope.DELIVERY.value,
                details={"error": str(e)},
            )
            return web.json_response({"error": "Internal server error"}, status=500)

    # ------------------------------------------------------------------
    # F46 — Worker-facing endpoints
    # ------------------------------------------------------------------

    @endpoint_metadata(
        auth=AuthTier.BRIDGE,
        risk=RiskTier.LOW,
        summary="Worker poll",
        description="Worker polls for pending jobs.",
        plane=RoutePlane.INTERNAL,
    )
    async def worker_poll_handler(self, request: web.Request) -> web.Response:
        """
        GET /bridge/worker/poll
        Worker polls for pending jobs. Returns available jobs or 204 if none.
        """
        is_valid, error_resp, device_id = require_bridge_auth(
            request, BridgeScope.JOB_STATUS
        )
        if not is_valid:
            return error_resp

        # Return pending jobs (FIFO, up to 5 per poll)
        try:
            batch_size = max(1, min(int(request.query.get("batch", "1")), 5))
        except (ValueError, TypeError):
            return web.json_response(
                {"error": "batch must be an integer (1-5)"}, status=400
            )
        jobs = []
        for _ in range(batch_size):
            if self._worker_job_queue:
                jobs.append(self._worker_job_queue.pop(0))
            else:
                break

        if not jobs:
            return web.Response(status=204)

        return web.json_response({"jobs": jobs})

    @endpoint_metadata(
        auth=AuthTier.BRIDGE,
        risk=RiskTier.MEDIUM,
        summary="Worker result",
        description="Worker submits completed job result.",
        audit="bridge.worker.result",
        plane=RoutePlane.INTERNAL,
    )
    async def worker_result_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/worker/result/{job_id}
        Worker submits completed job result.
        """
        is_valid, error_resp, device_id = require_bridge_auth(
            request, BridgeScope.JOB_SUBMIT
        )
        if not is_valid:
            self._audit(
                request=request,
                action="bridge.worker.result",
                target="bridge.worker.result",
                outcome="deny",
                status_code=getattr(error_resp, "status", 403),
                details={"reason": "auth_failed"},
            )
            return error_resp

        job_id = request.match_info.get("job_id", "")
        if not job_id:
            self._audit(
                request=request,
                action="bridge.worker.result",
                target="bridge.worker.result",
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"reason": "missing_job_id"},
            )
            return web.json_response({"error": "job_id required"}, status=400)

        # S50: Durable idempotency check for worker result ingress.
        # IMPORTANT: use check_and_record (durable path), do not rely on TTLCache-style get/put.
        idempotency_key = request.headers.get("X-Idempotency-Key", "")
        if idempotency_key:
            store_key = f"wr:{idempotency_key}"
            is_dup, _ = self._idempotency_store.check_and_record(store_key, ttl=86400)
            if is_dup:
                logger.info(
                    "Duplicate worker result suppressed for %s",
                    _bridge_sensitive_tag(idempotency_key, label="idem"),
                )
                return web.json_response(
                    {
                        "ok": True,
                        "job_id": job_id,
                        "status": "accepted",
                        "deduped": True,
                    }
                )

        try:
            data = await request.json()
        except Exception:
            self._audit(
                request=request,
                action="bridge.worker.result",
                target=job_id,
                outcome="deny",
                status_code=400,
                device_id=device_id,
                scope=BridgeScope.JOB_SUBMIT.value,
                details={"reason": "invalid_json"},
            )
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Store result
        self._worker_results[job_id] = {
            "status": data.get("status", "completed"),
            "outputs": data.get("outputs", {}),
            # IMPORTANT: keep worker identity redacted in cached bridge state.
            "worker_id": _bridge_sensitive_tag(device_id, label="device"),
            "timestamp": time.time(),
        }

        response_data = {"ok": True, "job_id": job_id, "status": "accepted"}

        self._audit(
            request=request,
            action="bridge.worker.result",
            target=job_id,
            outcome="allow",
            status_code=201,
            device_id=device_id,
            scope=BridgeScope.JOB_SUBMIT.value,
            details={"status": data.get("status", "completed")},
        )
        logger.info(
            "F46: Worker result accepted for job=%s from=%s",
            job_id,
            _bridge_sensitive_tag(device_id, label="device"),
        )
        return web.json_response(response_data, status=201)

    @endpoint_metadata(
        auth=AuthTier.BRIDGE,
        risk=RiskTier.LOW,
        summary="Worker heartbeat",
        description="Worker reports its status.",
        plane=RoutePlane.INTERNAL,
    )
    async def worker_heartbeat_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/worker/heartbeat
        Worker reports its status. Lightweight, no scope required.
        """
        # Basic auth only (no scope enforcement for heartbeat)
        is_valid, error_resp, device_id = require_bridge_auth(request, None)
        if not is_valid:
            return error_resp

        try:
            data = await request.json()
        except Exception:
            data = {}

        self._worker_heartbeats[device_id] = {
            "status": data.get("status", "alive"),
            "details": data.get("details", {}),
            "timestamp": time.time(),
        }

        return web.json_response({"ok": True})


def register_bridge_routes(
    app: web.Application, handlers: Optional[BridgeHandlers] = None
):
    """
    Register bridge routes with the aiohttp app.
    Uses contract-defined paths from BRIDGE_ENDPOINTS.
    """
    if handlers is None:
        handlers = BridgeHandlers()

    # Server-facing endpoints
    app.router.add_get(BRIDGE_ENDPOINTS["health"]["path"], handlers.health_handler)
    app.router.add_post(BRIDGE_ENDPOINTS["submit"]["path"], handlers.submit_handler)
    app.router.add_post(BRIDGE_ENDPOINTS["deliver"]["path"], handlers.deliver_handler)
    app.router.add_post(
        BRIDGE_ENDPOINTS["handshake"]["path"], handlers.handshake_handler
    )

    # F46: Worker-facing endpoints
    app.router.add_get(
        BRIDGE_ENDPOINTS["worker_poll"]["path"], handlers.worker_poll_handler
    )
    app.router.add_post(
        BRIDGE_ENDPOINTS["worker_result"]["path"] + "/{job_id}",
        handlers.worker_result_handler,
    )
    app.router.add_post(
        BRIDGE_ENDPOINTS["worker_heartbeat"]["path"],
        handlers.worker_heartbeat_handler,
    )

    logger.info(
        "Bridge routes registered: /bridge/{health,submit,deliver} "
        "+ /bridge/worker/{poll,result,heartbeat}"
    )
