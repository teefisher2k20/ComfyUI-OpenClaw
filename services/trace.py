"""
Trace Context Service (R25).
Provides trace ID generation, validation, and extraction utilities.
"""

import logging
import re
import uuid
from typing import Optional

from .legacy_compat import TRACE_ID_HEADERS, get_header_alias_value

logger = logging.getLogger("ComfyUI-OpenClaw.services.trace")

# Trace ID validation regex: Alphanumeric, dash, underscore, 1-64 chars
TRACE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Header name constant
TRACE_HEADER = TRACE_ID_HEADERS.primary
LEGACY_TRACE_HEADER = TRACE_ID_HEADERS.legacy


def generate_trace_id() -> str:
    """Generate a new trace ID (v4 UUID hex)."""
    return uuid.uuid4().hex


def validate_trace_id(trace_id: str) -> bool:
    """Check if trace_id is safe and valid."""
    if not trace_id:
        return False
    return bool(TRACE_ID_PATTERN.match(trace_id.strip()))


def normalize_trace_id(trace_id: Optional[str]) -> Optional[str]:
    """
    Normalize and validate a trace ID.
    Returns the trace_id if valid, None otherwise.
    """
    if not trace_id:
        return None
    trace_id = trace_id.strip()
    if not trace_id or len(trace_id) > 64:
        return None
    if not TRACE_ID_PATTERN.match(trace_id):
        return None
    return trace_id


def get_or_create_trace_id(user_provided_id: Optional[str] = None) -> str:
    """
    Get a valid trace ID.
    If user_provided_id is valid, return it.
    Otherwise, generate a new one.
    """
    if user_provided_id and validate_trace_id(user_provided_id):
        return user_provided_id

    if user_provided_id:
        # Log if we rejected an invalid ID (injection attempt?)
        # Limit logging frequency in high-traffic scenarios? (TODO)
        logger.warning(
            f"Invalid trace_id received: {user_provided_id[:64]}... Generating new."
        )

    new_id = generate_trace_id()
    # logger.debug(f"Generated trace_id: {new_id}")
    return new_id


def get_effective_trace_id(headers: dict, body_data: dict) -> str:
    """
    Extract trace ID from headers or body, or generate a new one.
    Priority:
    1. Header: X-OpenClaw-Trace-Id (or legacy X-Moltbot-Trace-Id)
    2. Body: trace_id (snake_case)
    3. Body: traceId (camelCase, for JS clients)
    4. Generated
    """
    header_trace, _used_legacy = get_header_alias_value(
        headers, TRACE_ID_HEADERS, logger=logger
    )

    body_trace = body_data.get("trace_id") or body_data.get("traceId")
    # Priority: Header > Body > Generated
    return get_or_create_trace_id(header_trace or body_trace)
