"""
R71 — Job Event Stream.

Bounded in-memory event store for job lifecycle transitions.
Provides an SSE endpoint for real-time job status delivery and
a JSON fallback endpoint for polling clients.

Events are derived from queue submission, history polling, and
callback delivery without patching ComfyUI core.

Design:
- Ring-buffer event store with configurable max capacity.
- Each event has a monotonic sequence ID for SSE `id:` field.
- Clients can resume from `Last-Event-ID` header.
- Access control parity with existing observability endpoints.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .reasoning_redaction import sanitize_operator_payload

logger = logging.getLogger("ComfyUI-OpenClaw.services.job_events")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_EVENT_BUFFER = int(os.environ.get("OPENCLAW_JOB_EVENT_BUFFER_SIZE", "500"))
EVENT_TTL_SEC = int(os.environ.get("OPENCLAW_JOB_EVENT_TTL_SEC", "600"))  # 10 minutes


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class JobEventType(Enum):
    """Lifecycle events for prompt/job tracking."""

    QUEUED = "queued"  # Job submitted to ComfyUI queue
    RUNNING = "running"  # Job execution started
    COMPLETED = "completed"  # Job finished successfully
    FAILED = "failed"  # Job failed with error
    CANCELLED = "cancelled"  # Job was cancelled
    CALLBACK_SENT = "callback_sent"  # Callback delivery succeeded
    CALLBACK_FAILED = "callback_failed"  # Callback delivery failed


# ---------------------------------------------------------------------------
# Event data class
# ---------------------------------------------------------------------------


@dataclass
class JobEvent:
    """A single job lifecycle event."""

    seq: int  # Monotonic sequence number (SSE id)
    event_type: str  # JobEventType.value
    prompt_id: str
    trace_id: str = ""
    timestamp: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_sse(self, *, include_reasoning: bool = False) -> str:
        """Format as an SSE event string."""
        payload = {
            "event_type": self.event_type,
            "prompt_id": self.prompt_id,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "data": sanitize_operator_payload(
                self.data, include_reasoning=include_reasoning
            ),
        }
        lines = [
            f"id: {self.seq}",
            f"event: {self.event_type}",
            f"data: {json.dumps(payload, separators=(',', ':'))}",
            "",
            "",
        ]
        return "\n".join(lines)

    def to_dict(self, *, include_reasoning: bool = False) -> Dict[str, Any]:
        """Serialise for JSON polling responses."""
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "prompt_id": self.prompt_id,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "data": sanitize_operator_payload(
                self.data, include_reasoning=include_reasoning
            ),
        }


# ---------------------------------------------------------------------------
# Bounded event store (ring buffer)
# ---------------------------------------------------------------------------


class JobEventStore:
    """
    Thread-safe bounded ring-buffer for job events.

    Supports:
    - emit(): add events
    - events_since(seq): retrieve events after a given sequence ID
    - SSE client resume via Last-Event-ID
    """

    def __init__(self, max_size: int = MAX_EVENT_BUFFER) -> None:
        try:
            # IMPORTANT: keep package-relative import first to avoid custom-node
            # import regressions when top-level "services" is unavailable.
            from .observability.backpressure import BoundedQueue
        except ImportError:
            # Fallback for ad-hoc/test import paths.
            from services.observability.backpressure import BoundedQueue

        self._lock = threading.Lock()
        self._queue = BoundedQueue[JobEvent](capacity=max_size)
        self._seq_counter = 0

    def emit(
        self,
        event_type: JobEventType,
        prompt_id: str,
        trace_id: str = "",
        data: Optional[Dict[str, Any]] = None,
    ) -> JobEvent:
        """Record a new job event and return it."""
        with self._lock:
            self._seq_counter += 1
            evt = JobEvent(
                seq=self._seq_counter,
                event_type=event_type.value,
                prompt_id=prompt_id,
                trace_id=trace_id,
                data=data or {},
            )

            # enqueue returns False if dropped, but we don't need to surface that
            # to the caller of emit(), tracking happens inside BoundedQueue checks.
            self._queue.enqueue(evt)
            return evt

    def events_since(
        self,
        last_seq: int = 0,
        limit: int = 100,
        prompt_id: Optional[str] = None,
    ) -> List[JobEvent]:
        """
        Return events with seq > last_seq, optionally filtered by prompt_id.
        Returns at most `limit` events (oldest first).
        """
        now = time.time()
        # No lock needed for get_all() as it is thread-safe, but we filter here.
        # Ideally get_all() returns a snapshot.
        all_events = self._queue.get_all()

        results = []
        for evt in all_events:
            if evt.seq <= last_seq:
                continue
            if now - evt.timestamp > EVENT_TTL_SEC:
                continue
            if prompt_id and evt.prompt_id != prompt_id:
                continue
            results.append(evt)
            if len(results) >= limit:
                break
        return results

    def events_since_bounded(
        self,
        *,
        last_seq: int = 0,
        limit: int = 100,
        prompt_id: Optional[str] = None,
        scan_cap: int = 2000,
    ) -> tuple[List[JobEvent], Dict[str, Any]]:
        """
        R95: Bounded scan variant of events_since() for management endpoints.

        Prevents full-buffer traversal when many entries are skipped due to TTL,
        prompt filter, or stale cursor ranges. Returns diagnostics so the API can
        surface deterministic pagination behavior.
        """
        now = time.time()
        all_events = self._queue.get_all()
        if scan_cap < 1:
            scan_cap = 1

        results: List[JobEvent] = []
        scanned = 0
        earliest_retained_seq: Optional[int] = None
        latest_retained_seq: Optional[int] = None

        for evt in all_events:
            if scanned >= scan_cap:
                break
            scanned += 1

            if evt.seq <= last_seq:
                continue
            if now - evt.timestamp > EVENT_TTL_SEC:
                continue
            if prompt_id and evt.prompt_id != prompt_id:
                continue

            if earliest_retained_seq is None:
                earliest_retained_seq = evt.seq
            latest_retained_seq = evt.seq

            results.append(evt)
            if len(results) >= limit:
                break

        # If we didn't collect any matching events, still compute retained bounds
        # from a second cheap pass to support stale cursor diagnostics.
        if earliest_retained_seq is None or latest_retained_seq is None:
            for evt in all_events:
                if evt.seq <= 0:
                    continue
                if now - evt.timestamp > EVENT_TTL_SEC:
                    continue
                if prompt_id and evt.prompt_id != prompt_id:
                    continue
                if earliest_retained_seq is None:
                    earliest_retained_seq = evt.seq
                latest_retained_seq = evt.seq

        return results, {
            "scanned": scanned,
            "scan_cap": scan_cap,
            "truncated": scanned >= scan_cap and len(results) < limit,
            "earliest_retained_seq": earliest_retained_seq,
            "latest_retained_seq": latest_retained_seq,
        }

    def latest_seq(self) -> int:
        """Return the latest sequence number."""
        with self._lock:
            return self._seq_counter

    @property
    def size(self) -> int:
        return self._queue.stats().current_size

    def stats(self) -> Dict[str, Any]:
        """Return drop/usage stats."""
        s = self._queue.stats()
        return {
            "capacity": s.capacity,
            "current_size": s.current_size,
            "high_watermark": s.high_watermark,
            "total_enqueued": s.total_enqueued,
            "total_dropped": s.total_dropped,
            "last_drop_ts": s.last_drop_ts,
        }

    def clear(self) -> None:
        """Clear all events (used in tests)."""
        with self._lock:
            self._queue.clear()
            self._seq_counter = 0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_store: Optional[JobEventStore] = None


def get_job_event_store() -> JobEventStore:
    """Get or create the global job event store."""
    global _store
    if _store is None:
        _store = JobEventStore()
    return _store


def reset_job_event_store() -> None:
    """Reset the global store (test utility)."""
    global _store
    _store = None
