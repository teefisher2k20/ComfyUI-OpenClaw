"""
Webhook Handler.
S2: ChatOps/webhook auth + least privilege.
S17: Rate limiting.

POST /moltbot/webhook
- Requires auth (deny-by-default)
- Accepts strict JobSpec
- Returns normalized internal request
"""

from __future__ import annotations

import json
import logging

try:
    from .errors import APIError, ErrorCode, create_error_response
except ImportError:
    # Build-time / Test fallback
    from api.errors import APIError, ErrorCode, create_error_response

try:
    from ..models.schemas import MAX_BODY_SIZE, WebhookJobRequest
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.metrics import metrics
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.trace import get_effective_trace_id
    from ..services.webhook_auth import get_auth_summary, require_auth
except ImportError:
    from models.schemas import MAX_BODY_SIZE, WebhookJobRequest
    from services.aiohttp_compat import import_aiohttp_web  # type: ignore
    from services.metrics import metrics
    from services.rate_limit import build_rate_limit_response, check_rate_limit
    from services.trace import get_effective_trace_id
    from services.webhook_auth import get_auth_summary, require_auth

try:
    from ..services.diagnostics_flags import diagnostics
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
except ImportError:
    from services.diagnostics_flags import diagnostics
    from services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )

# R46: Scoped logger for safe-by-default redaction
logger = diagnostics.get_logger("ComfyUI-OpenClaw.api.webhook", "webhook")
web = import_aiohttp_web()


@endpoint_metadata(
    auth=AuthTier.WEBHOOK,
    risk=RiskTier.HIGH,
    summary="Webhook submit",
    description="Authenticated endpoint for external job requests (legacy pipeline).",
    audit="webhook.submit.legacy",
    plane=RoutePlane.EXTERNAL,
)
async def webhook_handler(request: web.Request) -> web.Response:
    """
    POST /moltbot/webhook

    Authenticated endpoint for external job requests.
    """
    # S17: Rate Limit
    if not check_rate_limit(request, "webhook"):
        metrics.inc("webhook_denied")
        return build_rate_limit_response(
            request,
            "webhook",
            web_module=web,
            error="Rate limit exceeded",
            include_ok=True,
        )

    try:
        # Check content-type
        content_type = request.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            metrics.inc("webhook_denied")
            return create_error_response(
                message="Content-Type must be application/json",
                code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                status=415,
            )

        # Read raw body with size limit
        try:
            raw_body = await request.content.read(MAX_BODY_SIZE + 1)
            if len(raw_body) > MAX_BODY_SIZE:
                metrics.inc("webhook_denied")
                return create_error_response(
                    message=f"Payload too large (max {MAX_BODY_SIZE} bytes)",
                    code=ErrorCode.PAYLOAD_TOO_LARGE,
                    status=413,
                )
        except Exception as e:
            logger.error(f"Failed to read request body: {e}")
            metrics.inc("errors")
            return create_error_response(
                message="Failed to read request body",
                code=ErrorCode.READ_ERROR,
                status=400,
            )

        # Require auth
        valid, error = require_auth(request, raw_body)
        if not valid:
            # R46: Use debug log for details (safe redaction), warning for summary
            logger.debug(f"Webhook auth failed details", data={"error": error})
            logger.warning(f"Webhook auth failed: {error}")
            metrics.inc("webhook_denied")

            # Map error to appropriate status code
            status = (
                403
                if error
                in (
                    "auth_not_configured",
                    "bearer_not_configured",
                    "hmac_not_configured",
                )
                else 401
            )

            return create_error_response(
                message=error, code=ErrorCode.AUTH_FAILED, status=status
            )

        # Parse JSON
        try:
            data = json.loads(raw_body.decode("utf-8"))
            # R46: Log payload if validation diagnostics enabled
            if diagnostics.is_enabled("webhook.validate"):
                diagnostics.get_logger(
                    "ComfyUI-OpenClaw.api.webhook.validate", "webhook.validate"
                ).debug("Incoming Payload", data=data)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            metrics.inc("webhook_denied")
            return create_error_response(
                message="Invalid JSON", code=ErrorCode.INVALID_JSON, status=400
            )

        # Validate schema
        try:
            job_request = WebhookJobRequest.from_dict(data)
        except ValueError as e:
            metrics.inc("webhook_denied")
            return create_error_response(
                message="Validation Error",
                code=ErrorCode.VALIDATION_ERROR,
                status=400,
                detail={"error": str(e)},
            )
        except Exception as e:
            logger.error(f"Unexpected validation error: {e}")
            metrics.inc("errors")
            return create_error_response(
                message="Validation system error",
                code=ErrorCode.VALIDATION_ERROR,
                status=400,
            )

        # R25: Trace Context Extraction
        trace_id = get_effective_trace_id(request.headers, data)

        # Inject trace_id into flattened normalization if applicable,
        # or just ensure it's returned so caller can track it.
        # The job_request object *has* a trace_id field (we checked schemas.py).
        # But if it wasn't in input, it might be None.
        # We should set it on the object so to_normalized() includes it?
        if trace_id:
            job_request.trace_id = trace_id

        # Success - return normalized request
        metrics.inc("webhook_requests")

        normalized_data = job_request.to_normalized()
        # Ensure trace_id is in normalized data if not already
        if "trace_id" not in normalized_data or not normalized_data["trace_id"]:
            normalized_data["trace_id"] = trace_id

        return web.json_response(
            {
                "ok": True,
                "accepted": True,
                "trace_id": trace_id,
                "normalized": normalized_data,
            }
        )

    except Exception as e:
        # Catch-all for unexpected errors - log but don't expose details
        logger.exception(f"Unexpected webhook error: {e}")
        metrics.inc("errors")
        return create_error_response(
            message="Internal Server Error", code=ErrorCode.INTERNAL_ERROR, status=500
        )
