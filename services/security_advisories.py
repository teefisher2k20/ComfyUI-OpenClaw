"""
S48: Vulnerability advisory applicability evaluation.

Provides a deterministic local advisory model with semver range matching so
Security Doctor can surface affected/not-affected posture and mitigation hints.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.security_advisories")

_SEMVER_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?\s*$")
_COMPARATOR_RE = re.compile(r"^\s*(<=|>=|<|>|==|=)\s*(v?\d+\.\d+\.\d+(?:[-+].*)?)\s*$")

_HIGH_SEVERITIES = {"critical", "high"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_advisory_file() -> Path:
    return _repo_root() / "docs" / "release" / "security_advisories.json"


def parse_semver(version: str) -> Tuple[int, int, int]:
    """Parse `major.minor.patch` with optional pre-release/build suffixes."""
    match = _SEMVER_RE.match(str(version or "").strip())
    if not match:
        raise ValueError(f"Invalid semver string: {version!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _compare_versions(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> int:
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def _match_clause(version: Tuple[int, int, int], clause: str) -> bool:
    m = _COMPARATOR_RE.match(clause)
    if not m:
        raise ValueError(f"Invalid semver comparator clause: {clause!r}")

    op = m.group(1)
    target = parse_semver(m.group(2))
    cmp = _compare_versions(version, target)

    if op in ("=", "=="):
        return cmp == 0
    if op == "<":
        return cmp < 0
    if op == "<=":
        return cmp <= 0
    if op == ">":
        return cmp > 0
    if op == ">=":
        return cmp >= 0
    raise ValueError(f"Unsupported comparator: {op!r}")


def is_version_in_range(version: str, range_expr: str) -> bool:
    """
    Check if `version` matches a comma-separated comparator expression.

    Example:
    - `>=0.2.0,<0.2.5`
    - `==1.0.1`
    """
    ver = parse_semver(version)
    clauses = [c.strip() for c in str(range_expr or "").split(",") if c.strip()]
    if not clauses:
        return False
    return all(_match_clause(ver, clause) for clause in clauses)


def _normalize_entries(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        items = raw.get("advisories")
        if isinstance(items, list):
            return [dict(item) for item in items if isinstance(item, dict)]
    return []


def load_advisories(advisory_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = advisory_file or default_advisory_file()
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse advisory file '%s': %s", path, exc)
        return []

    return _normalize_entries(payload)


def evaluate_advisories(
    *,
    current_version: str,
    advisories: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    high_affected = 0
    first_mitigation = ""

    for item in advisories:
        advisory_id = str(item.get("id") or item.get("advisory_id") or "").strip()
        if not advisory_id:
            continue
        severity = str(item.get("severity") or "unknown").strip().lower()
        affected_range = str(item.get("affected_range") or "").strip()
        mitigation = str(item.get("mitigation") or "").strip()
        fixed_version = str(item.get("fixed_version") or "").strip()
        summary = str(item.get("summary") or "").strip()
        url = str(item.get("url") or "").strip()

        affected = False
        if affected_range:
            try:
                affected = is_version_in_range(current_version, affected_range)
            except ValueError:
                logger.warning(
                    "Invalid advisory range for %s: %s", advisory_id, affected_range
                )

        if affected:
            if not first_mitigation and mitigation:
                first_mitigation = mitigation
            if severity in _HIGH_SEVERITIES:
                high_affected += 1

        entries.append(
            {
                "id": advisory_id,
                "severity": severity,
                "summary": summary,
                "affected_range": affected_range,
                "fixed_version": fixed_version,
                "mitigation": mitigation,
                "url": url,
                "affected": affected,
            }
        )

    affected_any = any(bool(entry.get("affected")) for entry in entries)
    return {
        "current_version": current_version,
        "affected": affected_any,
        "high_severity_affected": high_affected,
        "mitigation": first_mitigation,
        "advisories": entries,
    }


def build_advisory_status(
    *,
    current_version: str,
    advisory_file: Optional[Path] = None,
) -> Dict[str, Any]:
    advisories = load_advisories(advisory_file=advisory_file)
    return evaluate_advisories(current_version=current_version, advisories=advisories)

