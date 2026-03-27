"""
R90: Compatibility matrix governance helpers.

Machine-readable metadata and refresh workflow primitives for
`docs/release/compatibility_matrix.md`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

META_BLOCK_TAG = "openclaw-compat-matrix-meta"
DEFAULT_WARN_AGE_DAYS = 30
DEFAULT_MAX_AGE_DAYS = 45
ANCHOR_KEYS = ("comfyui", "comfyui_frontend", "desktop")

META_BLOCK_RE = re.compile(
    r"```" + re.escape(META_BLOCK_TAG) + r"\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)
SEMVER_RE = re.compile(r"(?P<version>\d+\.\d+\.\d+)")
DESKTOP_ANCHOR_RE = re.compile(
    r"^(?P<desktop>\d+\.\d+\.\d+)\s+\(core\s+(?P<core>\d+\.\d+\.\d+)\s+/\s+frontend\s+(?P<frontend>\d+\.\d+\.\d+)\)$"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _utc_now().date().isoformat()


def _parse_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


def _json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_metadata() -> Dict[str, Any]:
    today = _today_iso()
    return {
        "schema_version": 1,
        "matrix_version": "v0.2.1",
        "last_validated_date": today,
        "policy": {
            "warn_age_days": DEFAULT_WARN_AGE_DAYS,
            "max_age_days": DEFAULT_MAX_AGE_DAYS,
        },
        "anchors": {key: "unknown" for key in ANCHOR_KEYS},
        "evidence": {
            "evidence_id": f"compat-matrix-{today.replace('-', '')}",
            "updated_at": _utc_now().isoformat(),
            "updated_by": "manual",
        },
    }


def format_metadata_block(metadata: Dict[str, Any]) -> str:
    return (
        f"```{META_BLOCK_TAG}\n"
        + json.dumps(metadata, indent=2, sort_keys=True)
        + "\n```\n"
    )


def extract_metadata_block(
    text: str,
) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str]]:
    """
    Extract JSON metadata block.

    Returns: (metadata, issues, raw_json_text)
    """
    match = META_BLOCK_RE.search(text)
    if not match:
        return None, ["R90_META_BLOCK_MISSING"], None

    raw = match.group("body").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, ["R90_META_BLOCK_INVALID_JSON"], raw
    if not isinstance(parsed, dict):
        return None, ["R90_META_BLOCK_NOT_OBJECT"], raw
    return parsed, [], raw


def replace_metadata_block(text: str, metadata: Dict[str, Any]) -> str:
    block = format_metadata_block(metadata)
    if META_BLOCK_RE.search(text):
        return META_BLOCK_RE.sub(lambda _m: block.rstrip("\n"), text, count=1)

    # Insert after first heading if present; otherwise prepend.
    lines = text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("# "):
            return "".join(lines[: idx + 1] + ["\n", block] + lines[idx + 1 :])
    return block + text


def _body_without_meta(text: str) -> str:
    return META_BLOCK_RE.sub("", text).strip()


def read_matrix_document(path: Path | str) -> Dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    metadata, issues, raw = extract_metadata_block(text)
    return {
        "path": str(p),
        "text": text,
        "metadata": metadata,
        "issues": issues,
        "raw_metadata": raw,
        "body_sha256": hashlib.sha256(
            _body_without_meta(text).encode("utf-8")
        ).hexdigest(),
        "has_meta": metadata is not None,
    }


def validate_metadata(
    metadata: Optional[Dict[str, Any]], *, today: Optional[date] = None
) -> Dict[str, Any]:
    today = today or _utc_now().date()
    violations: List[Dict[str, Any]] = []
    if not isinstance(metadata, dict):
        return {
            "ok": False,
            "status": "invalid",
            "code": "R90_META_INVALID",
            "age_days": None,
            "violations": [{"code": "R90_META_MISSING", "message": "Metadata missing"}],
        }

    schema_version = metadata.get("schema_version")
    if schema_version != 1:
        violations.append(
            {
                "code": "R90_META_SCHEMA_VERSION",
                "message": f"Unsupported schema_version: {schema_version!r}",
            }
        )

    last_validated = metadata.get("last_validated_date")
    parsed_last = (
        _parse_date(last_validated) if isinstance(last_validated, str) else None
    )
    if parsed_last is None:
        violations.append(
            {
                "code": "R90_META_LAST_VALIDATED_DATE",
                "message": "Missing/invalid last_validated_date (YYYY-MM-DD)",
            }
        )

    policy = metadata.get("policy")
    if not isinstance(policy, dict):
        policy = {}
        violations.append(
            {"code": "R90_META_POLICY", "message": "Missing policy object"}
        )

    try:
        warn_age_days = int(policy.get("warn_age_days", DEFAULT_WARN_AGE_DAYS))
    except Exception:
        warn_age_days = DEFAULT_WARN_AGE_DAYS
        violations.append(
            {"code": "R90_META_WARN_AGE", "message": "Invalid policy.warn_age_days"}
        )
    try:
        max_age_days = int(policy.get("max_age_days", DEFAULT_MAX_AGE_DAYS))
    except Exception:
        max_age_days = DEFAULT_MAX_AGE_DAYS
        violations.append(
            {"code": "R90_META_MAX_AGE", "message": "Invalid policy.max_age_days"}
        )
    if warn_age_days < 0 or max_age_days < 0 or warn_age_days > max_age_days:
        violations.append(
            {
                "code": "R90_META_AGE_POLICY_ORDER",
                "message": "Age policy must satisfy 0 <= warn_age_days <= max_age_days",
            }
        )

    anchors = metadata.get("anchors")
    if not isinstance(anchors, dict):
        anchors = {}
        violations.append(
            {"code": "R90_META_ANCHORS", "message": "Missing anchors object"}
        )
    else:
        for key in ANCHOR_KEYS:
            if key not in anchors:
                violations.append(
                    {
                        "code": "R90_META_ANCHOR_MISSING",
                        "message": f"Missing anchors.{key}",
                    }
                )

    age_days: Optional[int] = None
    if parsed_last is not None:
        age_days = (today - parsed_last).days
        if age_days < 0:
            violations.append(
                {
                    "code": "R90_META_FUTURE_DATE",
                    "message": f"last_validated_date is in the future: {last_validated}",
                }
            )

    if violations:
        status = "invalid"
        code = "R90_META_INVALID"
    else:
        assert age_days is not None
        if age_days > max_age_days:
            status = "stale"
            code = "R90_MATRIX_STALE"
        elif age_days > warn_age_days:
            status = "warning"
            code = "R90_MATRIX_AGING"
        else:
            status = "fresh"
            code = "R90_MATRIX_FRESH"

    return {
        "ok": len(violations) == 0,
        "status": status,
        "code": code,
        "age_days": age_days,
        "warn_age_days": warn_age_days,
        "max_age_days": max_age_days,
        "violations": violations,
    }


def normalize_observed_anchors(
    *,
    comfyui: Optional[str] = None,
    comfyui_frontend: Optional[str] = None,
    desktop: Optional[str] = None,
) -> Dict[str, str]:
    return {
        "comfyui": (comfyui or "").strip() or "unknown",
        "comfyui_frontend": (comfyui_frontend or "").strip() or "unknown",
        "desktop": (desktop or "").strip() or "unknown",
    }


def _extract_semver(anchor: Optional[str]) -> Optional[str]:
    if not isinstance(anchor, str):
        return None
    match = SEMVER_RE.search(anchor)
    if not match:
        return None
    return match.group("version")


def _parse_semver(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not isinstance(version, str):
        return None
    try:
        major, minor, patch = version.split(".")
        return int(major), int(minor), int(patch)
    except Exception:
        return None


def _compare_semver(left: Optional[str], right: Optional[str]) -> Optional[int]:
    left_tuple = _parse_semver(left)
    right_tuple = _parse_semver(right)
    if left_tuple is None or right_tuple is None:
        return None
    if left_tuple == right_tuple:
        return 0
    return 1 if left_tuple > right_tuple else -1


def build_host_surface_contract(
    published_anchors: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    anchors = dict(published_anchors or {})
    standalone_anchor = str(anchors.get("comfyui_frontend", "unknown"))
    desktop_anchor = str(anchors.get("desktop", "unknown"))
    standalone_frontend_version = _extract_semver(standalone_anchor)

    desktop_match = DESKTOP_ANCHOR_RE.match(desktop_anchor)
    desktop_version = None
    desktop_core_version = None
    desktop_embedded_frontend_version = None
    violations: List[Dict[str, str]] = []

    if desktop_anchor != "unknown" and desktop_match is None:
        violations.append(
            {
                "code": "R164_DESKTOP_ANCHOR_PARSE",
                "message": "Desktop anchor did not match the expected bundle format",
            }
        )
    elif desktop_match is not None:
        desktop_version = desktop_match.group("desktop")
        desktop_core_version = desktop_match.group("core")
        desktop_embedded_frontend_version = desktop_match.group("frontend")

    compare_result = _compare_semver(
        desktop_embedded_frontend_version, standalone_frontend_version
    )
    if compare_result is None:
        desktop_frontend_status = "unknown"
    elif compare_result == 0:
        desktop_frontend_status = "in_sync"
    elif compare_result < 0:
        desktop_frontend_status = "lagging"
    else:
        desktop_frontend_status = "ahead"

    return {
        "ok": len(violations) == 0,
        "code": (
            "R164_HOST_SURFACES_READY"
            if not violations
            else "R164_HOST_SURFACE_CONTRACT_INVALID"
        ),
        "surfaces": {
            "standalone_frontend": {
                "anchor": standalone_anchor,
                "frontend_version": standalone_frontend_version,
            },
            "desktop": {
                "anchor": desktop_anchor,
                "desktop_version": desktop_version,
                "core_version": desktop_core_version,
                "embedded_frontend_version": desktop_embedded_frontend_version,
                "frontend_parity": {
                    "status": desktop_frontend_status,
                    "reference_frontend_version": standalone_frontend_version,
                },
            },
        },
        "violations": violations,
    }


def detect_anchor_drift(
    published_anchors: Optional[Dict[str, Any]],
    observed_anchors: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    drift: List[Dict[str, str]] = []
    pub = published_anchors or {}
    obs = observed_anchors or {}
    for key in ANCHOR_KEYS:
        published = str(pub.get(key, "unknown"))
        observed = str(obs.get(key, "unknown"))
        if observed == "unknown":
            continue
        if published == "unknown":
            drift.append(
                {
                    "anchor": key,
                    "status": "untracked",
                    "published": published,
                    "observed": observed,
                }
            )
            continue
        if published != observed:
            drift.append(
                {
                    "anchor": key,
                    "status": "drift",
                    "published": published,
                    "observed": observed,
                }
            )
    return {
        "ok": len(drift) == 0,
        "code": "R90_ANCHORS_IN_SYNC" if not drift else "R90_ANCHOR_DRIFT",
        "drift": drift,
    }


@dataclass
class RefreshWorkflowResult:
    ok: bool
    matrix_path: str
    run_date: str
    stages: Dict[str, Any]
    decision_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "matrix_path": self.matrix_path,
            "run_date": self.run_date,
            "stages": copy.deepcopy(self.stages),
            "decision_codes": list(self.decision_codes),
        }


def run_refresh_workflow(
    *,
    matrix_path: Path | str,
    observed_anchors: Optional[Dict[str, str]] = None,
    apply: bool = False,
    updated_by: str = "script",
    today: Optional[date] = None,
) -> RefreshWorkflowResult:
    p = Path(matrix_path)
    today = today or _utc_now().date()
    observed = dict(observed_anchors or normalize_observed_anchors())

    doc = read_matrix_document(p)
    metadata = (
        copy.deepcopy(doc["metadata"]) if isinstance(doc["metadata"], dict) else None
    )
    if metadata is None:
        metadata = _default_metadata()
        # Preserve compatibility for first adoption while making missing metadata visible.
        bootstrap_mode = True
    else:
        bootstrap_mode = False

    validate_before = validate_metadata(doc["metadata"], today=today)
    drift_before = detect_anchor_drift(metadata.get("anchors"), observed)

    collect_stage = {
        "matrix_exists": p.exists(),
        "metadata_present": doc["has_meta"],
        "body_sha256": doc["body_sha256"],
        "observed_anchors": observed,
        "doc_issues": list(doc["issues"]),
    }
    diff_stage = {
        "metadata_hash_before": (
            _json_hash(doc["metadata"]) if doc["metadata"] is not None else None
        ),
        "drift": drift_before,
        "bootstrap_metadata": bootstrap_mode,
    }
    validate_stage = {
        "before": validate_before,
    }

    publish_stage: Dict[str, Any] = {"mode": "dry-run", "updated": False}
    updated_text = doc["text"]
    metadata_after = copy.deepcopy(metadata)
    metadata_after.setdefault("policy", {})
    metadata_after.setdefault("anchors", {})
    metadata_after.setdefault("evidence", {})
    metadata_after["last_validated_date"] = today.isoformat()
    for key in ANCHOR_KEYS:
        metadata_after["anchors"][key] = observed.get(key, "unknown")
    metadata_after["evidence"]["updated_by"] = updated_by
    metadata_after["evidence"]["updated_at"] = _utc_now().isoformat()
    metadata_after["evidence"][
        "evidence_id"
    ] = f"compat-matrix-refresh-{today.strftime('%Y%m%d')}"

    validate_after = validate_metadata(metadata_after, today=today)
    drift_after = detect_anchor_drift(metadata_after.get("anchors"), observed)
    validate_stage["after"] = validate_after

    if apply:
        updated_text = replace_metadata_block(doc["text"], metadata_after)
        p.write_text(updated_text, encoding="utf-8")
        publish_stage = {
            "mode": "apply",
            "updated": True,
            "metadata_hash_after": _json_hash(metadata_after),
            "drift_after": drift_after,
            "body_sha256_after": hashlib.sha256(
                _body_without_meta(updated_text).encode("utf-8")
            ).hexdigest(),
        }
    else:
        publish_stage = {
            "mode": "dry-run",
            "updated": False,
            "metadata_preview_hash": _json_hash(metadata_after),
            "drift_after": drift_after,
        }

    decision_codes: List[str] = []
    decision_codes.append(validate_after["code"])
    decision_codes.append(drift_before["code"])
    if bootstrap_mode:
        decision_codes.append("R90_BOOTSTRAP_METADATA")
    if apply:
        decision_codes.append("R90_PUBLISH_APPLY")
    else:
        decision_codes.append("R90_PUBLISH_DRY_RUN")

    ok = bool(validate_after["ok"])
    stages = {
        "collect": collect_stage,
        "diff": diff_stage,
        "validate": validate_stage,
        "publish": publish_stage,
    }
    return RefreshWorkflowResult(
        ok=ok,
        matrix_path=str(p),
        run_date=today.isoformat(),
        stages=stages,
        decision_codes=decision_codes,
    )
