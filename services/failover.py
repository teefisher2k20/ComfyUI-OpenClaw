"""
Failover Routing Layer (R14).

Provides intelligent provider/model failover with error classification,
cooldown management, and bounded retry logic.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.failover")

# R37: Storm control constants
DEDUPE_WINDOW_SEC = 2.0  # Suppress duplicate events within 2 seconds
MIN_CANDIDATE_INTERVAL_SEC = 2.0  # Minimum interval between attempts
DEFAULT_HEALTH_SCORE = 70  # Start neutral (range 0-100)


# Error categories for failover decisions
class ErrorCategory(Enum):
    AUTH = "auth"  # Authentication failed (401, 403)
    BILLING = "billing"  # Billing/quota exceeded (402, 429 billing)
    RATE_LIMIT = "rate_limit"  # Rate limit (429 non-billing)
    TIMEOUT = "timeout"  # Request timeout
    INVALID_REQUEST = "invalid_request"  # Bad request (400, 422)
    UNKNOWN = "unknown"  # Other errors


@dataclass
class CooldownEntry:
    """Cooldown state for a provider/model combination."""

    provider: str
    model: Optional[str]
    reason: str
    until: float  # Unix timestamp when cooldown expires

    def is_active(self) -> bool:
        """Check if cooldown is still active."""
        return time.time() < self.until


class FailoverState:
    """
    Manages cooldown state persistence.
    State is stored in openclaw_state/failover.json.
    """

    def __init__(self, state_file: Optional[str] = None):
        """
        Initialize failover state manager.

        Args:
            state_file: Path to state file. Defaults to openclaw_state/failover.json.
        """
        if state_file is None:
            # Default to openclaw_state/failover.json
            try:
                from ..services.state_dir import get_state_dir
            except ImportError:
                from services.state_dir import get_state_dir

            state_dir = get_state_dir()
            state_file = os.path.join(state_dir, "failover.json")

        self.state_file = state_file
        self.cooldowns: Dict[str, CooldownEntry] = {}
        # IMPORTANT: relative in-memory windows must use monotonic time to avoid
        # NTP/system clock adjustments causing duplicate/throttle false positives.
        self._window_clock = time.monotonic
        # R37: Storm control state
        self.dedupe_map: Dict[str, float] = {}  # (provider:model:category) -> last_ts
        self.health_scores: Dict[str, int] = {}  # (provider:model) -> score [0-100]
        self.last_attempts: Dict[str, float] = {}  # (provider:model) -> last_attempt_ts
        self._load()

    def _load(self) -> None:
        """Load cooldown state from disk."""
        if not os.path.exists(self.state_file):
            return
        # Empty file can happen if a previous run crashed mid-write; treat as no state.
        try:
            if os.path.getsize(self.state_file) == 0:
                return
        except OSError:
            return

        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)

            # Reconstruct cooldown entries
            self.cooldowns = {}
            for key, entry_data in data.items():
                entry = CooldownEntry(**entry_data)
                # Only keep active cooldowns
                if entry.is_active():
                    self.cooldowns[key] = entry

            # Save back to remove expired entries
            self._save()

        except Exception as e:
            logger.error(f"Failed to load failover state: {e}")
            self.cooldowns = {}

    def _save(self) -> None:
        """Save cooldown state to disk (no secrets)."""
        try:
            # Ensure directory exists
            state_dir = os.path.dirname(self.state_file) or "."
            os.makedirs(state_dir, exist_ok=True)

            # Serialize active cooldowns only
            data = {
                key: asdict(entry)
                for key, entry in self.cooldowns.items()
                if entry.is_active()
            }

            # R67: atomic write (.tmp + replace) to reduce partial-file corruption on
            # process interruption and keep reset/shutdown flows consistent.
            fd, temp_path = tempfile.mkstemp(
                suffix=".json", dir=state_dir, prefix="failover_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(temp_path, self.state_file)
            except Exception:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass
                raise

        except Exception as e:
            logger.error(f"Failed to save failover state: {e}")

    def flush(self) -> None:
        """Persist active cooldown state immediately (best effort)."""
        self._save()

    def _get_key(self, provider: str, model: Optional[str]) -> str:
        """Generate a unique key for provider/model combination."""
        if model:
            return f"{provider}:{model}"
        return provider

    def set_cooldown(
        self, provider: str, model: Optional[str], reason: str, duration_sec: float
    ) -> None:
        """
        Set a cooldown for a provider/model.

        Args:
            provider: Provider name.
            model: Optional model name.
            reason: Human-readable reason (no secrets).
            duration_sec: Cooldown duration in seconds.
        """
        key = self._get_key(provider, model)
        until = time.time() + duration_sec

        self.cooldowns[key] = CooldownEntry(
            provider=provider, model=model, reason=reason, until=until
        )
        self._save()
        logger.info(f"Set cooldown for {key}: {reason} (until {until})")

    def is_cooling_down(self, provider: str, model: Optional[str]) -> bool:
        """
        Check if provider/model is in cooldown.

        Args:
            provider: Provider name.
            model: Optional model name.

        Returns:
            True if in cooldown, False otherwise.
        """
        key = self._get_key(provider, model)
        entry = self.cooldowns.get(key)

        if entry is None:
            return False

        if not entry.is_active():
            # Expired, remove it
            del self.cooldowns[key]
            self._save()
            return False

        return True

    def clear_cooldown(self, provider: str, model: Optional[str]) -> None:
        """Clear cooldown for a provider/model."""
        key = self._get_key(provider, model)
        if key in self.cooldowns:
            del self.cooldowns[key]
            self._save()

    # R37: Storm control methods

    def _get_dedupe_key(
        self, provider: str, model: Optional[str], category: ErrorCategory
    ) -> str:
        """Get dedupe key for (provider, model, category)."""
        base_key = self._get_key(provider, model)
        return f"{base_key}:{category.value}"

    def should_suppress_duplicate(
        self, provider: str, model: Optional[str], category: ErrorCategory
    ) -> bool:
        """
        Check if this error should be suppressed (duplicate within window).

        Returns:
            True if this is a duplicate (suppress), False if new (process)
        """
        dedupe_key = self._get_dedupe_key(provider, model, category)
        last_ts = self.dedupe_map.get(dedupe_key, 0)
        now = self._window_clock()

        if now - last_ts < DEDUPE_WINDOW_SEC:
            # Duplicate within window
            return True

        # New event, update timestamp
        self.dedupe_map[dedupe_key] = now
        return False

    def get_health_score(self, provider: str, model: Optional[str]) -> int:
        """Get current health score for provider/model [0-100]."""
        key = self._get_key(provider, model)
        return self.health_scores.get(key, DEFAULT_HEALTH_SCORE)

    def update_health_score(
        self,
        provider: str,
        model: Optional[str],
        category: ErrorCategory,
        is_success: bool = False,
    ) -> None:
        """
        Update health score based on outcome.

        Args:
            provider: Provider name
            model: Model name
            category: Error category (if failure)
            is_success: True if successful request
        """
        key = self._get_key(provider, model)
        current_score = self.get_health_score(provider, model)

        if is_success:
            new_score = min(100, current_score + 1)
        elif category == ErrorCategory.RATE_LIMIT:
            new_score = max(0, current_score - 3)
        elif category == ErrorCategory.TIMEOUT:
            new_score = max(0, current_score - 2)
        elif category in (
            ErrorCategory.AUTH,
            ErrorCategory.BILLING,
            ErrorCategory.INVALID_REQUEST,
        ):
            new_score = max(0, current_score - 10)
        else:  # UNKNOWN
            new_score = max(0, current_score - 1)

        self.health_scores[key] = new_score
        logger.debug(f"Health score for {key}: {current_score} -> {new_score}")

    def can_attempt_now(self, provider: str, model: Optional[str]) -> bool:
        """Check if enough time has passed since last attempt (throttle)."""
        key = self._get_key(provider, model)
        last_attempt = self.last_attempts.get(key, 0)
        return self._window_clock() - last_attempt >= MIN_CANDIDATE_INTERVAL_SEC

    def mark_attempt(self, provider: str, model: Optional[str]) -> None:
        """Mark current time as last attempt."""
        key = self._get_key(provider, model)
        self.last_attempts[key] = self._window_clock()


# Global failover state instance
_failover_state: Optional[FailoverState] = None


def get_failover_state() -> FailoverState:
    """Get or create the global failover state instance."""
    global _failover_state
    if _failover_state is None:
        _failover_state = FailoverState()
    return _failover_state


def reset_failover_state(*, flush: bool = False) -> None:
    """Reset global failover state singleton (tests / controlled reset helper)."""
    global _failover_state
    if _failover_state is not None and flush:
        try:
            _failover_state.flush()
        except Exception:
            logger.exception("R67: failover flush during reset failed")
    _failover_state = None


def classify_error(
    error: Exception, status_code: Optional[int] = None
) -> Tuple[ErrorCategory, Optional[int]]:
    """
    Classify an error into a failover category and extract retry-after.

    Args:
        error: Exception raised.
        status_code: Optional HTTP status code (may be in exception).

    Returns:
        Tuple of (ErrorCategory, retry_after_seconds or None)
    """
    # R14/R37: Check if error is ProviderHTTPError
    try:
        from services.provider_errors import ProviderHTTPError

        if isinstance(error, ProviderHTTPError):
            status_code = error.status_code
            retry_after = error.retry_after
        else:
            retry_after = None
    except ImportError:
        retry_after = None

    error_str = str(error).lower()

    # Status code-based classification
    if status_code:
        if status_code == 401 or status_code == 403:
            category = ErrorCategory.AUTH
        elif status_code == 402:
            category = ErrorCategory.BILLING
        elif status_code == 429:
            # Distinguish rate limit vs billing
            if "quota" in error_str or "billing" in error_str:
                category = ErrorCategory.BILLING
            else:
                category = ErrorCategory.RATE_LIMIT
        elif status_code == 400 or status_code == 422:
            category = ErrorCategory.INVALID_REQUEST
        else:
            category = ErrorCategory.UNKNOWN
    else:
        # Exception type-based classification
        if "timeout" in error_str or "timed out" in error_str:
            category = ErrorCategory.TIMEOUT
        elif "unauthorized" in error_str or "forbidden" in error_str:
            category = ErrorCategory.AUTH
        elif "rate limit" in error_str or "too many requests" in error_str:
            category = ErrorCategory.RATE_LIMIT
        elif "quota" in error_str or "insufficient" in error_str:
            category = ErrorCategory.BILLING
        else:
            category = ErrorCategory.UNKNOWN

    return category, retry_after


def should_retry(category: ErrorCategory) -> bool:
    """
    Determine if we should retry the same provider/model.

    Args:
        category: Error category.

    Returns:
        True if retry is recommended, False if failover is better.
    """
    # Retry for transient errors
    if category in (ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT):
        return True

    # Don't retry for auth/billing/invalid request
    return False


def should_failover(category: ErrorCategory) -> bool:
    """
    Determine if we should fail over to another provider/model.

    Args:
        category: Error category.

    Returns:
        True if failover is recommended.
    """
    # Failover for auth, billing, invalid request
    if category in (
        ErrorCategory.AUTH,
        ErrorCategory.BILLING,
        ErrorCategory.INVALID_REQUEST,
    ):
        return True

    # Also failover for persistent unknowns
    if category == ErrorCategory.UNKNOWN:
        return True

    return False


def get_cooldown_duration(
    category: ErrorCategory, retry_after_override: Optional[int] = None
) -> float:
    """
    Get cooldown duration for an error category.

    Args:
        category: Error category.
        retry_after_override: Optional retry-after hint from upstream (seconds).
                             If provided and category is retriable, use this instead of default.

    Returns:
        Cooldown duration in seconds (clamped to [1, 3600]).
    """
    # R14/R37: Prefer retry-after for retriable errors
    if retry_after_override is not None and category in (
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.TIMEOUT,
    ):
        # Use upstream hint, already clamped by retry_after.py
        return float(retry_after_override)

    # Fallback: Conservative defaults
    durations = {
        ErrorCategory.AUTH: 3600,  # 1 hour (likely needs config fix)
        ErrorCategory.BILLING: 1800,  # 30 minutes
        ErrorCategory.RATE_LIMIT: 300,  # 5 minutes
        ErrorCategory.TIMEOUT: 60,  # 1 minute
        ErrorCategory.INVALID_REQUEST: 600,  # 10 minutes
        ErrorCategory.UNKNOWN: 120,  # 2 minutes
    }
    return durations.get(category, 120)


def get_failover_candidates(
    primary_provider: str,
    primary_model: Optional[str],
    fallback_models: Optional[List[str]] = None,
    fallback_providers: Optional[List[str]] = None,
) -> List[Tuple[str, Optional[str]]]:
    """
    Get ordered list of failover candidates.

    Args:
        primary_provider: Primary provider name.
        primary_model: Primary model name.
        fallback_models: Optional list of fallback models (same provider).
        fallback_providers: Optional list of fallback providers.

    Returns:
        List of (provider, model) tuples in priority order.
    """
    candidates = [(primary_provider, primary_model)]

    # Add model fallbacks on same provider
    if fallback_models:
        for model in fallback_models:
            if model != primary_model:
                candidates.append((primary_provider, model))

    # Add provider fallbacks
    if fallback_providers:
        for provider in fallback_providers:
            if provider != primary_provider:
                # Use same model name if possible, else None
                candidates.append((provider, primary_model))

    return candidates
