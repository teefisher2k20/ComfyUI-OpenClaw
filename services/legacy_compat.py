"""
Legacy compatibility registry and helpers (R149).

Centralizes alias metadata so deprecation handling is not re-implemented ad hoc
across unrelated modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

OPENCLAW_API_PREFIX = "/openclaw"
LEGACY_API_PREFIX = "/moltbot"
API_PREFIXES = (OPENCLAW_API_PREFIX, LEGACY_API_PREFIX)


@dataclass(frozen=True)
class HeaderAlias:
    primary: str
    legacy: str


ADMIN_TOKEN_HEADERS = HeaderAlias(
    primary="X-OpenClaw-Admin-Token",
    legacy="X-Moltbot-Admin-Token",
)
OBS_TOKEN_HEADERS = HeaderAlias(
    primary="X-OpenClaw-Obs-Token",
    legacy="X-Moltbot-Obs-Token",
)
TRACE_ID_HEADERS = HeaderAlias(
    primary="X-OpenClaw-Trace-Id",
    legacy="X-Moltbot-Trace-Id",
)
WEBHOOK_SIGNATURE_HEADERS = HeaderAlias(
    primary="X-OpenClaw-Signature",
    legacy="X-Moltbot-Signature",
)
WEBHOOK_TIMESTAMP_HEADERS = HeaderAlias(
    primary="X-OpenClaw-Timestamp",
    legacy="X-Moltbot-Timestamp",
)
WEBHOOK_NONCE_HEADERS = HeaderAlias(
    primary="X-OpenClaw-Nonce",
    legacy="X-Moltbot-Nonce",
)


def _header_value(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is None:
        return ""
    return str(value).strip()


def _increment_legacy_api_hits() -> None:
    try:
        from .metrics import metrics
    except ImportError:
        metrics = None
    if metrics:
        metrics.inc("legacy_api_hits")


def emit_legacy_header_warning(
    alias: HeaderAlias,
    *,
    logger: Optional[logging.Logger] = None,
) -> None:
    _increment_legacy_api_hits()
    active_logger = logger or logging.getLogger(
        "ComfyUI-OpenClaw.services.legacy_compat"
    )
    active_logger.warning(
        "DEPRECATION WARNING: Legacy header %s used. Please migrate to %s.",
        alias.legacy,
        alias.primary,
    )


def get_header_alias_value(
    headers: Mapping[str, str],
    alias: HeaderAlias,
    *,
    logger: Optional[logging.Logger] = None,
) -> Tuple[str, bool]:
    """
    Return the canonical header value, falling back to the legacy alias.

    Returns `(value, used_legacy)` and emits the standard deprecation warning
    exactly when the legacy header supplied the effective value.
    """
    value = _header_value(headers, alias.primary)
    if value:
        return value, False

    legacy_value = _header_value(headers, alias.legacy)
    if legacy_value:
        emit_legacy_header_warning(alias, logger=logger)
        return legacy_value, True

    return "", False


def get_api_path_candidates(path: str) -> Tuple[str, ...]:
    if path.startswith(OPENCLAW_API_PREFIX + "/"):
        return (path, path.replace(OPENCLAW_API_PREFIX, LEGACY_API_PREFIX, 1))
    if path.startswith(LEGACY_API_PREFIX + "/"):
        return (path, path.replace(LEGACY_API_PREFIX, OPENCLAW_API_PREFIX, 1))
    return (path,)
