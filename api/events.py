"""
R71 — Job Event Stream Endpoint.

SSE (Server-Sent Events) endpoint for real-time job lifecycle delivery,
plus a JSON polling fallback endpoint.

Routes:
  GET /openclaw/events/stream  — SSE (text/event-stream)
  GET /openclaw/events         — JSON polling fallback
"""

from __future__ import annotations

import asyncio
import json
import logging
from inspect import signature
from typing import Any, Dict

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    web = None  # type: ignore

if __package__ and "." in __package__:
    from ..services.access_control import (
        require_admin_token,
        require_observability_access,
    )
    from ..services.job_events import get_job_event_store
    from ..services.management_query import normalize_cursor_limit
    from ..services.metrics import metrics
    from ..services.rate_limit import build_rate_limit_response, check_rate_limit
    from ..services.reasoning_redaction import (
        audit_reasoning_reveal,
        resolve_reasoning_reveal,
    )
else:  # pragma: no cover
    from services.access_control import (  # type: ignore
        require_admin_token,
        require_observability_access,
    )
    from services.job_events import get_job_event_store  # type: ignore
    from services.management_query import normalize_cursor_limit  # type: ignore
    from services.metrics import metrics  # type: ignore
    from services.rate_limit import (  # type: ignore
        build_rate_limit_response,
        check_rate_limit,
    )
    from services.reasoning_redaction import (  # type: ignore
        audit_reasoning_reveal,
        resolve_reasoning_reveal,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.events")

# SSE keep-alive interval (seconds)
SSE_KEEPALIVE_SEC = 15
# Maximum SSE connection duration (seconds) — prevents zombie connections
SSE_MAX_DURATION_SEC = 300  # 5 minutes


def _call_event_serializer(
    event: Any,
    method_name: str,
    *,
    include_reasoning: bool,
) -> Any:
    """Use enhanced serializers when supported, but stay compatible with old test doubles."""
    serializer = getattr(event, method_name)
    try:
        params = signature(serializer).parameters
    except (TypeError, ValueError):
        params = {}
    if "include_reasoning" in params:
        return serializer(include_reasoning=include_reasoning)
    return serializer()


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


@endpoint_metadata(
    auth=AuthTier.OBSERVABILITY,
    risk=RiskTier.LOW,
    summary="Stream job events",
    description="SSE endpoint for job lifecycle events.",
    audit="events.stream",
    plane=RoutePlane.ADMIN,
)
async def events_stream_handler(request: web.Request) -> web.StreamResponse:
    """
    GET /openclaw/events/stream

    SSE endpoint for job lifecycle events.
    Supports Last-Event-ID for resume.
    Access control parity with observability endpoints.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    # Rate limit
    if not check_rate_limit(request, "events"):
        return build_rate_limit_response(
            request,
            "events",
            web_module=web,
            error="rate_limit_exceeded",
            include_ok=True,
        )

    # Access control (same as logs/tail)
    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)
    admin_allowed, _ = require_admin_token(request)
    reveal = resolve_reasoning_reveal(request, admin_authorized=admin_allowed)
    audit_reasoning_reveal(request, target="events.stream", decision=reveal)

    store = get_job_event_store()

    # Parse Last-Event-ID for resume
    last_seq = 0
    last_event_id = request.headers.get("Last-Event-ID", "").strip()
    if last_event_id:
        try:
            last_seq = int(last_event_id)
        except ValueError:
            pass

    # Optional prompt_id filter
    prompt_id = request.query.get("prompt_id")

    # Set up SSE response
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    metrics.inc("events_sse_connections")

    import time

    start_time = time.time()

    last_keepalive = time.time()

    try:
        while True:
            # Check max duration
            if time.time() - start_time > SSE_MAX_DURATION_SEC:
                break

            events = get_job_event_store().events_since(
                last_seq=last_seq,
                limit=50,
                prompt_id=prompt_id,
            )

            if events:
                for evt in events:
                    await response.write(
                        _call_event_serializer(
                            evt,
                            "to_sse",
                            include_reasoning=reveal["allowed"],
                        ).encode("utf-8")
                    )
                    last_seq = evt.seq
            else:
                # Send keep-alive header only if interval exceeded
                now = time.time()
                if now - last_keepalive > SSE_KEEPALIVE_SEC:
                    await response.write(b": keepalive\n\n")
                    last_keepalive = now

            # Poll interval (1s latency is acceptable for job events)
            await asyncio.sleep(1)

    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        metrics.inc("events_sse_disconnections")

    return response


@endpoint_metadata(
    auth=AuthTier.OBSERVABILITY,
    risk=RiskTier.LOW,
    summary="Poll job events",
    description="JSON polling fallback for job events.",
    audit="events.poll",
    plane=RoutePlane.ADMIN,
)
async def events_poll_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/events

    JSON polling fallback for job events.
    Query params:
      - since: sequence number to resume from (default 0)
      - prompt_id: optional filter
      - limit: max events to return (default 50, max 200)
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    # Rate limit
    if not check_rate_limit(request, "events"):
        return build_rate_limit_response(
            request,
            "events",
            web_module=web,
            error="rate_limit_exceeded",
            include_ok=True,
        )

    # Access control
    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)
    admin_allowed, _ = require_admin_token(request)
    reveal = resolve_reasoning_reveal(request, admin_authorized=admin_allowed)
    audit_reasoning_reveal(request, target="events.poll", decision=reveal)

    store = get_job_event_store()

    # R95: deterministic pagination normalization + bounded scan diagnostics
    prompt_id = request.query.get("prompt_id")
    page = normalize_cursor_limit(
        request.query,
        cursor_key="since",
        default_cursor=0,
        min_cursor=0,
        default_limit=50,
        max_limit=200,
    )
    since_requested = int(page.cursor or 0)
    latest_seq = store.latest_seq()

    cursor_status = "ok"
    since_effective = since_requested
    if since_requested > latest_seq:
        cursor_status = "future_cursor_reset"
        since_effective = latest_seq
        page.warnings.append(
            {
                "code": "R95_STALE_CURSOR_FUTURE",
                "field": "since",
                "raw": str(since_requested),
                "normalized": since_effective,
            }
        )

    scan_cap = max(page.limit * 10, 500)
    events, scan = store.events_since_bounded(
        last_seq=since_effective,
        limit=page.limit,
        prompt_id=prompt_id,
        scan_cap=scan_cap,
    )

    earliest_retained = scan.get("earliest_retained_seq")
    if (
        isinstance(earliest_retained, int)
        and since_effective != 0
        and since_effective < (earliest_retained - 1)
    ):
        cursor_status = "stale_cursor_reset"
        since_effective = max(0, earliest_retained - 1)
        page.warnings.append(
            {
                "code": "R95_STALE_CURSOR_RESET",
                "field": "since",
                "raw": str(since_requested),
                "normalized": since_effective,
            }
        )
        events, scan = store.events_since_bounded(
            last_seq=since_effective,
            limit=page.limit,
            prompt_id=prompt_id,
            scan_cap=scan_cap,
        )

    return web.json_response(
        {
            "ok": True,
            "events": [
                _call_event_serializer(
                    e,
                    "to_dict",
                    include_reasoning=reveal["allowed"],
                )
                for e in events
            ],
            "latest_seq": latest_seq,
            "pagination": {
                "limit": page.limit,
                "since_requested": since_requested,
                "since_effective": since_effective,
                "cursor_status": cursor_status,
                "warnings": page.warnings,
            },
            "delta": {
                "cursor_key": "since",
                "requested_since_seq": since_requested,
                "effective_since_seq": since_effective,
                "next_since_seq": (events[-1].seq if events else since_effective),
                "latest_seq": latest_seq,
                "earliest_retained_seq": scan.get("earliest_retained_seq"),
                "latest_retained_seq": scan.get("latest_retained_seq"),
                "cursor_status": cursor_status,
                "snapshot": since_requested == 0,
                "truncated": bool(
                    scan.get("truncated")
                    or (
                        events
                        and isinstance(scan.get("latest_retained_seq"), int)
                        and int(scan.get("latest_retained_seq")) > int(events[-1].seq)
                    )
                ),
                "warnings": page.warnings,
            },
            "scan": scan,
        }
    )
