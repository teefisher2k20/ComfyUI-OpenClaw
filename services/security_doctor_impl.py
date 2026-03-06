"""
S30 + S32 — ComfyUI-Aware Security Doctor.

Deploy-time and runtime security diagnostics specific to ComfyUI extension operations.
Read-only checks by default; optional guarded remediation for safe/local actions only.

Checks:
- Endpoint exposure: detect non-loopback access without token
- Token boundaries: admin vs observability token posture
- SSRF posture: callback_url / base_url allowlist wildcard misuse
- State-dir permissions: writable, world-readable checks
- Redaction drift: verify redaction patterns cover known sensitive keys
- ComfyUI runtime mode: Desktop/portable/venv compatibility
- Feature flag posture: high-risk features default-off check
- S32 connector security posture: token, allowlist, callback, DM policy

Usage:
    from services.security_doctor import run_security_doctor
    report = run_security_doctor()
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import platform
import stat
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.security_doctor")

try:
    from .connector_allowlist_posture import (
        evaluate_connector_allowlist_posture,
        is_strict_connector_allowlist_profile,
    )
except Exception:
    from services.connector_allowlist_posture import (  # type: ignore
        evaluate_connector_allowlist_posture,
        is_strict_connector_allowlist_profile,
    )

try:
    from ..config import PACK_VERSION
except Exception:
    try:
        from config import PACK_VERSION  # type: ignore
    except Exception:
        PACK_VERSION = "0.0.0"

try:
    from .security_advisories import build_advisory_status
except Exception:
    from services.security_advisories import build_advisory_status  # type: ignore

# ---------------------------------------------------------------------------
# WP1: S30 Violation Code Mapping Table (bounded vocabulary)
# ---------------------------------------------------------------------------

VIOLATION_CODE_MAP: Dict[str, str] = {
    # Endpoint exposure
    "endpoint_exposure": "SEC-EP-001",
    "admin_token_missing": "SEC-EP-002",
    "public_shared_surface_boundary": "SEC-BD-001",
    # Token boundaries
    "token_reuse": "SEC-TK-001",
    "admin_token_weak": "SEC-TK-002",
    "observability_token_weak": "SEC-TK-002",
    # SSRF
    "callback_wildcard": "SEC-SR-001",
    "base_url_private_ip": "SEC-SR-002",
    # State directory
    "state_dir_world_writable": "SEC-SD-001",
    "state_dir_world_readable": "SEC-SD-002",
    "state_dir_writable": "SEC-SD-003",
    "secrets_file_perms": "SEC-SD-004",
    # Redaction
    "redaction_coverage": "SEC-RD-001",
    # Runtime
    "venv_isolation": "SEC-RT-001",
    "python_security": "SEC-RT-002",
    # Feature flags
    "high_risk_flags": "SEC-FF-001",
    # API key
    "api_key_length": "SEC-AK-001",
    # Connector
    "s32_allowlist_coverage": "SEC-CN-001",
    # Wave 2
    "s35_isolation": "SEC-W2-001",
    "r77_integrity": "SEC-W2-002",
    # S45 exposure parity
    "s45_exposed_no_auth": "SEC-S45-001",
    "s45_dangerous_override": "SEC-S45-002",
    "s45_hardened_loopback_no_admin": "SEC-S45-003",
    # S66 runtime guardrails
    "s66_runtime_guardrails": "SEC-S66-001",
    # S68 CSRF no-origin override posture
    "csrf_no_origin_override": "SEC-CSRF-001",
    # S48 vulnerability advisory surfacing
    "vulnerability_advisories": "SEC-VA-001",
}

# Reason codes that trigger high_risk_mode
_HIGH_RISK_CODES = {"SEC-S45-001", "SEC-S45-002", "SEC-FF-001", "SEC-VA-001"}

# ---------------------------------------------------------------------------
# Severity + Result types (reuse operator_doctor patterns)
# ---------------------------------------------------------------------------


class SecuritySeverity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    INFO = "info"


@dataclass
class SecurityCheckResult:
    """Result of a single security diagnostic check."""

    name: str
    severity: str  # SecuritySeverity.value
    message: str
    category: str = ""  # e.g. "endpoint", "token", "ssrf", "state_dir", "redaction"
    detail: str = ""
    remediation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "category": self.category,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.remediation:
            d["remediation"] = self.remediation
        return d


@dataclass
class SecurityReport:
    """Aggregated security diagnostic report."""

    checks: List[SecurityCheckResult] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    summary: Dict[str, int] = field(default_factory=dict)
    remediation_applied: List[str] = field(default_factory=list)
    advisory_status: Dict[str, Any] = field(default_factory=dict)

    def add(self, result: SecurityCheckResult) -> None:
        self.checks.append(result)

    def build_summary(self) -> None:
        counts: Dict[str, int] = {}
        for c in self.checks:
            counts[c.severity] = counts.get(c.severity, 0) + 1
        self.summary = counts

    @property
    def has_failures(self) -> bool:
        return any(c.severity == SecuritySeverity.FAIL.value for c in self.checks)

    @property
    def risk_score(self) -> int:
        """Compute a simple risk score: FAIL=10, WARN=3, INFO/PASS/SKIP=0."""
        score = 0
        for c in self.checks:
            if c.severity == SecuritySeverity.FAIL.value:
                score += 10
            elif c.severity == SecuritySeverity.WARN.value:
                score += 3
        return score

    def _build_violations(self) -> List[Dict[str, Any]]:
        """Build normalized violation list from fail/warn checks with stable codes."""
        violations: List[Dict[str, Any]] = []
        for c in self.checks:
            if c.severity not in (
                SecuritySeverity.FAIL.value,
                SecuritySeverity.WARN.value,
            ):
                continue
            code = VIOLATION_CODE_MAP.get(c.name)
            if not code:
                continue  # unmapped checks are not surfaced as violations
            entry: Dict[str, Any] = {
                "code": code,
                "severity": c.severity,
                "check": c.name,
                "message": c.message,
            }
            if c.remediation:
                entry["remediation"] = c.remediation
            violations.append(entry)
        return violations

    def _compute_posture(self) -> str:
        """Deterministic posture: 'fail' if ANY check has fail severity, else 'pass'.

        Scans self.checks directly — not just mapped violations — so that
        unmapped fail checks also force posture='fail'.
        """
        return "fail" if self.has_failures else "pass"

    @staticmethod
    def _compute_high_risk(violations: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Determine high-risk mode from violation codes."""
        reasons: List[str] = []
        seen: set = set()
        for v in violations:
            code = v["code"]
            if code in _HIGH_RISK_CODES and code not in seen:
                seen.add(code)
                reasons.append(code)
        return (bool(reasons), reasons)

    def to_dict(self) -> Dict[str, Any]:
        self.build_summary()
        violations = self._build_violations()
        high_risk, hr_reasons = self._compute_high_risk(violations)
        return {
            # Legacy fields (preserved for backward compat)
            "environment": self.environment,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary,
            "risk_score": self.risk_score,
            "remediation_applied": self.remediation_applied,
            # S30 contract fields
            "schema_version": "1.0",
            "posture": self._compute_posture(),
            "high_risk_mode": high_risk,
            "high_risk_reasons": hr_reasons,
            "violations": violations,
            # S48 advisory surfacing
            "advisory_status": dict(self.advisory_status),
        }

    def to_human(self) -> str:
        """Human-readable security report."""
        self.build_summary()
        lines: List[str] = []
        lines.append("=" * 64)
        lines.append("  OpenClaw Security Doctor Report")
        lines.append("=" * 64)
        lines.append("")

        # Environment
        lines.append("Environment:")
        for k, v in self.environment.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

        # Group by category
        categories: Dict[str, List[SecurityCheckResult]] = {}
        for c in self.checks:
            cat = c.category or "general"
            categories.setdefault(cat, []).append(c)

        for cat, checks in categories.items():
            lines.append(f"  [{cat.upper()}]")
            for c in checks:
                icon = {
                    "pass": "✓",
                    "warn": "⚠",
                    "fail": "✗",
                    "skip": "○",
                    "info": "ℹ",
                }.get(c.severity, "?")
                lines.append(f"    [{icon}] {c.name}: {c.message}")
                if c.detail:
                    lines.append(f"        Detail: {c.detail}")
                if c.remediation:
                    lines.append(f"        Fix: {c.remediation}")
            lines.append("")

        # Risk score
        lines.append("-" * 64)
        lines.append(f"  Risk Score: {self.risk_score}")
        total = sum(self.summary.values())
        lines.append(
            f"  Total: {total}  |  "
            f"Fail: {self.summary.get('fail', 0)}  |  "
            f"Warn: {self.summary.get('warn', 0)}  |  "
            f"Pass: {self.summary.get('pass', 0)}  |  "
            f"Skip: {self.summary.get('skip', 0)}"
        )
        if self.remediation_applied:
            lines.append(f"  Remediations applied: {len(self.remediation_applied)}")
            for r in self.remediation_applied:
                lines.append(f"    - {r}")
        lines.append("=" * 64)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pack root detection
# ---------------------------------------------------------------------------


def _get_pack_root() -> Path:
    """Detect the ComfyUI-OpenClaw pack root directory."""
    this_dir = Path(__file__).resolve().parent
    candidate = this_dir.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    return Path.cwd()


# ---------------------------------------------------------------------------
# Security checks — Endpoint exposure
# ---------------------------------------------------------------------------


def check_endpoint_exposure(report: SecurityReport) -> None:
    """Check if endpoints are exposed without token protection."""
    admin_token = os.environ.get("OPENCLAW_ADMIN_TOKEN") or os.environ.get(
        "MOLTBOT_ADMIN_TOKEN"
    )
    obs_token = os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN") or os.environ.get(
        "MOLTBOT_OBSERVABILITY_TOKEN"
    )

    if not admin_token and not obs_token:
        report.add(
            SecurityCheckResult(
                name="endpoint_exposure",
                severity=SecuritySeverity.WARN.value,
                message="No admin or observability tokens configured — loopback-only mode",
                category="endpoint",
                detail="All admin/observability endpoints require loopback access.",
                remediation="Set OPENCLAW_ADMIN_TOKEN and OPENCLAW_OBSERVABILITY_TOKEN for remote deployments.",
            )
        )
    elif not admin_token:
        report.add(
            SecurityCheckResult(
                name="admin_token_missing",
                severity=SecuritySeverity.WARN.value,
                message="No admin token — config/secrets endpoints in convenience mode",
                category="endpoint",
                remediation="Set OPENCLAW_ADMIN_TOKEN for production deployments.",
            )
        )
    else:
        report.add(
            SecurityCheckResult(
                name="admin_token_set",
                severity=SecuritySeverity.PASS.value,
                message="Admin token configured",
                category="endpoint",
            )
        )

    if obs_token:
        report.add(
            SecurityCheckResult(
                name="observability_token_set",
                severity=SecuritySeverity.PASS.value,
                message="Observability token configured",
                category="endpoint",
            )
        )


def check_public_shared_surface_boundary(report: SecurityReport) -> None:
    """S69: Surface shared ComfyUI/OpenClaw listener boundary posture."""
    profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local").strip().lower()
    if not profile:
        profile = "local"

    ack_raw = (
        os.environ.get("OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK")
        or os.environ.get("MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK")
        or ""
    ).strip()
    ack = ack_raw.lower() in {"1", "true", "yes", "on"}

    report.environment["deployment_profile"] = profile
    report.environment["public_shared_surface_boundary_ack"] = (
        "enabled" if ack else "off"
    )

    if profile != "public":
        report.add(
            SecurityCheckResult(
                name="public_shared_surface_boundary",
                severity=SecuritySeverity.PASS.value,
                message="Shared-surface boundary acknowledgement not required outside public profile",
                category="endpoint",
                detail=f"profile={profile}",
            )
        )
        return

    if ack:
        report.add(
            SecurityCheckResult(
                name="public_shared_surface_boundary",
                severity=SecuritySeverity.PASS.value,
                message="Public shared-surface boundary acknowledgement is enabled",
                category="endpoint",
                remediation=(
                    "Keep reverse-proxy path allowlist + network ACL controls aligned with this acknowledgement."
                ),
            )
        )
        return

    report.add(
        SecurityCheckResult(
            name="public_shared_surface_boundary",
            severity=SecuritySeverity.WARN.value,
            message="Public profile boundary acknowledgement is missing for shared ComfyUI/OpenClaw surface",
            category="endpoint",
            remediation=(
                "Set OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1 only after reverse-proxy "
                "path allowlist and network ACL deny ComfyUI-native high-risk routes."
            ),
        )
    )


# ---------------------------------------------------------------------------
# Security checks — Token boundaries
# ---------------------------------------------------------------------------


def check_token_boundaries(report: SecurityReport) -> None:
    """Verify admin and observability tokens are distinct."""
    admin_token = (
        os.environ.get("OPENCLAW_ADMIN_TOKEN")
        or os.environ.get("MOLTBOT_ADMIN_TOKEN")
        or ""
    ).strip()
    obs_token = (
        os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN")
        or os.environ.get("MOLTBOT_OBSERVABILITY_TOKEN")
        or ""
    ).strip()

    if admin_token and obs_token and admin_token == obs_token:
        report.add(
            SecurityCheckResult(
                name="token_reuse",
                severity=SecuritySeverity.FAIL.value,
                message="Admin and observability tokens are identical — privilege confusion risk",
                category="token",
                remediation="Use distinct tokens for admin and observability access.",
            )
        )
    elif admin_token and obs_token:
        report.add(
            SecurityCheckResult(
                name="token_separation",
                severity=SecuritySeverity.PASS.value,
                message="Admin and observability tokens are distinct",
                category="token",
            )
        )

    # Check token strength (minimum length)
    for label, token in [("admin", admin_token), ("observability", obs_token)]:
        if token and len(token) < 16:
            report.add(
                SecurityCheckResult(
                    name=f"{label}_token_weak",
                    severity=SecuritySeverity.WARN.value,
                    message=f"{label.title()} token is short ({len(token)} chars) — consider longer tokens",
                    category="token",
                    remediation=f"Use a {label} token of at least 16 characters.",
                )
            )


# ---------------------------------------------------------------------------
# Security checks — SSRF posture
# ---------------------------------------------------------------------------


def check_ssrf_posture(report: SecurityReport) -> None:
    """Check callback/base_url configurations for SSRF risk indicators."""
    # CRITICAL: keep canonical *_ALLOW_HOSTS keys first. Runtime callback policy
    # and deployment-profile checks use these names; drifting to legacy-only
    # aliases makes Security Doctor miss live SSRF posture violations.
    callback_allowlist = (
        os.environ.get("OPENCLAW_CALLBACK_ALLOW_HOSTS", "").strip()
        or os.environ.get("MOLTBOT_CALLBACK_ALLOW_HOSTS", "").strip()
        or os.environ.get("OPENCLAW_CALLBACK_ALLOWLIST", "").strip()
        or os.environ.get("MOLTBOT_CALLBACK_ALLOWLIST", "").strip()
    )
    if callback_allowlist:
        hosts = [h.strip() for h in callback_allowlist.split(",") if h.strip()]
        if any("*" in host for host in hosts):
            report.add(
                SecurityCheckResult(
                    name="callback_wildcard",
                    severity=SecuritySeverity.FAIL.value,
                    message="Callback allowlist contains overly broad wildcards",
                    category="ssrf",
                    detail=f"Allowlist: {callback_allowlist}",
                    remediation="Use specific hostnames instead of wildcards in callback allowlists.",
                )
            )
        else:
            report.add(
                SecurityCheckResult(
                    name="callback_allowlist",
                    severity=SecuritySeverity.PASS.value,
                    message=f"Callback allowlist configured with {len(hosts)} host(s)",
                    category="ssrf",
                )
            )

    # Check base_url configuration
    try:
        from .state_dir import get_state_dir

        config_path = os.path.join(get_state_dir(), "config.json")
    except Exception:
        try:
            from services.state_dir import get_state_dir

            config_path = os.path.join(get_state_dir(), "config.json")
        except Exception:
            config_path = None

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            base_url = cfg.get("base_url", "")
            if base_url:
                # Check for private IP in base_url
                try:
                    from urllib.parse import urlparse

                    parsed = urlparse(base_url)
                    host = parsed.hostname or ""
                    try:
                        ip = ipaddress.ip_address(host)
                        if ip.is_private and not ip.is_loopback:
                            report.add(
                                SecurityCheckResult(
                                    name="base_url_private_ip",
                                    severity=SecuritySeverity.WARN.value,
                                    message=f"LLM base_url points to private IP ({host})",
                                    category="ssrf",
                                    remediation="Ensure this is an intentional local LLM setup.",
                                )
                            )
                    except ValueError:
                        pass  # hostname, not IP — OK
                except Exception:
                    pass
        except Exception:
            pass

    report.add(
        SecurityCheckResult(
            name="ssrf_posture",
            severity=SecuritySeverity.PASS.value,
            message="SSRF posture check completed",
            category="ssrf",
        )
    )


# ---------------------------------------------------------------------------
# Security checks — State directory permissions
# ---------------------------------------------------------------------------


def check_state_dir_permissions(report: SecurityReport) -> None:
    """Check state directory for unsafe permissions."""
    try:
        from .state_dir import get_state_dir

        state_dir = get_state_dir()
    except Exception:
        try:
            from services.state_dir import get_state_dir

            state_dir = get_state_dir()
        except Exception:
            state_dir = os.environ.get("OPENCLAW_STATE_DIR") or os.environ.get(
                "MOLTBOT_STATE_DIR"
            )

    if not state_dir:
        report.add(
            SecurityCheckResult(
                name="state_dir_perms",
                severity=SecuritySeverity.SKIP.value,
                message="State directory not configured — using defaults",
                category="state_dir",
            )
        )
        return

    p = Path(state_dir)
    if not p.exists():
        report.add(
            SecurityCheckResult(
                name="state_dir_exists",
                severity=SecuritySeverity.INFO.value,
                message=f"State dir does not exist yet: {state_dir}",
                category="state_dir",
            )
        )
        return

    # Writable check
    if not os.access(str(p), os.W_OK):
        report.add(
            SecurityCheckResult(
                name="state_dir_writable",
                severity=SecuritySeverity.FAIL.value,
                message=f"State dir not writable: {state_dir}",
                category="state_dir",
                remediation="Check file permissions on the state directory.",
            )
        )
        return

    # Platform-specific permission checks
    if platform.system() != "Windows":
        try:
            st = os.stat(str(p))
            mode = st.st_mode
            # Check for world-readable or world-writable
            if mode & stat.S_IROTH:
                report.add(
                    SecurityCheckResult(
                        name="state_dir_world_readable",
                        severity=SecuritySeverity.WARN.value,
                        message="State directory is world-readable",
                        category="state_dir",
                        detail=f"Permissions: {oct(mode)}",
                        remediation="Run: chmod 700 " + state_dir,
                    )
                )
            if mode & stat.S_IWOTH:
                report.add(
                    SecurityCheckResult(
                        name="state_dir_world_writable",
                        severity=SecuritySeverity.FAIL.value,
                        message="State directory is world-writable — critical security risk",
                        category="state_dir",
                        detail=f"Permissions: {oct(mode)}",
                        remediation="Run: chmod 700 " + state_dir,
                    )
                )
        except Exception:
            pass

    # Check for secrets files with open permissions
    secrets_file = p / "secrets.json"
    if secrets_file.exists() and platform.system() != "Windows":
        try:
            st = os.stat(str(secrets_file))
            if st.st_mode & (stat.S_IROTH | stat.S_IWOTH):
                report.add(
                    SecurityCheckResult(
                        name="secrets_file_perms",
                        severity=SecuritySeverity.FAIL.value,
                        message="Secrets file has world-accessible permissions",
                        category="state_dir",
                        remediation="Run: chmod 600 " + str(secrets_file),
                    )
                )
        except Exception:
            pass

    report.add(
        SecurityCheckResult(
            name="state_dir_check",
            severity=SecuritySeverity.PASS.value,
            message=f"State directory permissions OK: {state_dir}",
            category="state_dir",
        )
    )


# ---------------------------------------------------------------------------
# Security checks — Redaction drift
# ---------------------------------------------------------------------------


def check_redaction_drift(report: SecurityReport) -> None:
    """Verify that redaction patterns cover expected sensitive keys."""
    try:
        from .redaction import SENSITIVE_KEYS
    except ImportError:
        try:
            from services.redaction import SENSITIVE_KEYS
        except ImportError:
            report.add(
                SecurityCheckResult(
                    name="redaction_module",
                    severity=SecuritySeverity.SKIP.value,
                    message="Redaction module not available",
                    category="redaction",
                )
            )
            return

    # Expected minimum set of sensitive keys
    expected_keys = {
        "api_key",
        "password",
        "secret",
        "token",
        "authorization",
        "private_key",
    }

    missing = expected_keys - SENSITIVE_KEYS
    if missing:
        report.add(
            SecurityCheckResult(
                name="redaction_coverage",
                severity=SecuritySeverity.WARN.value,
                message=f"Redaction missing expected sensitive keys: {missing}",
                category="redaction",
                remediation="Update services/redaction.py SENSITIVE_KEYS to include missing keys.",
            )
        )
    else:
        report.add(
            SecurityCheckResult(
                name="redaction_coverage",
                severity=SecuritySeverity.PASS.value,
                message=f"Redaction covers all {len(expected_keys)} expected sensitive keys",
                category="redaction",
            )
        )


# ---------------------------------------------------------------------------
# Security checks — ComfyUI runtime mode
# ---------------------------------------------------------------------------


def check_comfyui_runtime(report: SecurityReport) -> None:
    """Check ComfyUI runtime mode compatibility."""
    in_venv = sys.prefix != sys.base_prefix
    report.environment["in_venv"] = str(in_venv)
    report.environment["os"] = platform.system()

    if not in_venv:
        report.add(
            SecurityCheckResult(
                name="venv_isolation",
                severity=SecuritySeverity.WARN.value,
                message="Not running in a virtual environment — shared system packages risk",
                category="runtime",
                remediation="Use a project-local .venv for dependency isolation.",
            )
        )

    # Check for ComfyUI Desktop indicators
    desktop_indicators = [
        os.environ.get("COMFYUI_DESKTOP"),
        os.environ.get("ELECTRON_RUN_AS_NODE"),
    ]
    if any(desktop_indicators):
        report.environment["runtime_mode"] = "desktop"
        report.add(
            SecurityCheckResult(
                name="desktop_mode",
                severity=SecuritySeverity.INFO.value,
                message="ComfyUI Desktop mode detected",
                category="runtime",
                detail="Desktop mode may restrict file access and network behavior.",
            )
        )
    else:
        report.environment["runtime_mode"] = "standard"

    # Check Python version for security support
    ver = sys.version_info
    if ver.major == 3 and ver.minor < 10:
        report.add(
            SecurityCheckResult(
                name="python_security",
                severity=SecuritySeverity.WARN.value,
                message=f"Python {ver.major}.{ver.minor} may lack security patches",
                category="runtime",
                remediation="Upgrade to Python 3.10+ for active security support.",
            )
        )


# ---------------------------------------------------------------------------
# Security checks — Feature flag posture
# ---------------------------------------------------------------------------

# High-risk feature flags that should be OFF by default
HIGH_RISK_FLAGS = {
    "OPENCLAW_ENABLE_REMOTE_ADMIN": "Remote admin access",
    "OPENCLAW_ENABLE_BRIDGE": "Sidecar bridge",
    "OPENCLAW_ENABLE_TRANSFORMS": "Constrained transforms (F42)",
    "OPENCLAW_ENABLE_REGISTRY_SYNC": "Remote registry sync (F41)",
    "MOLTBOT_DEV_MODE": "Development mode (auth bypass)",
}


def check_feature_flags(report: SecurityReport) -> None:
    """Check that high-risk features are not accidentally enabled."""
    enabled_flags = []
    for env_key, label in HIGH_RISK_FLAGS.items():
        val = os.environ.get(env_key, "").strip().lower()
        if val in ("1", "true", "yes", "on"):
            enabled_flags.append(f"{env_key} ({label})")

    if enabled_flags:
        report.add(
            SecurityCheckResult(
                name="high_risk_flags",
                severity=SecuritySeverity.WARN.value,
                message=f"{len(enabled_flags)} high-risk feature flag(s) enabled",
                category="feature_flags",
                detail="; ".join(enabled_flags),
                remediation="Disable high-risk flags unless explicitly required for your deployment.",
            )
        )
    else:
        report.add(
            SecurityCheckResult(
                name="high_risk_flags",
                severity=SecuritySeverity.PASS.value,
                message="All high-risk features disabled (default-off)",
                category="feature_flags",
            )
        )


# ---------------------------------------------------------------------------
# Security checks — API key posture
# ---------------------------------------------------------------------------


def check_api_key_posture(report: SecurityReport) -> None:
    """Check API key configuration for common issues."""
    api_key = (
        os.environ.get("OPENCLAW_LLM_API_KEY")
        or os.environ.get("MOLTBOT_LLM_API_KEY")
        or os.environ.get("CLAWDBOT_LLM_API_KEY")
        or ""
    )

    if not api_key:
        report.add(
            SecurityCheckResult(
                name="api_key_present",
                severity=SecuritySeverity.INFO.value,
                message="No LLM API key in environment — may use stored key or local LLM",
                category="api_key",
            )
        )
    else:
        # Never log the key — just check properties
        if len(api_key) < 10:
            report.add(
                SecurityCheckResult(
                    name="api_key_length",
                    severity=SecuritySeverity.WARN.value,
                    message="LLM API key appears unusually short",
                    category="api_key",
                    remediation="Verify the API key is complete and valid.",
                )
            )
        else:
            report.add(
                SecurityCheckResult(
                    name="api_key_present",
                    severity=SecuritySeverity.PASS.value,
                    message="LLM API key configured via environment",
                    category="api_key",
                )
            )


# ---------------------------------------------------------------------------
# Security checks — S48 vulnerability advisory posture
# ---------------------------------------------------------------------------


def check_vulnerability_advisories(report: SecurityReport) -> None:
    """S48: Surface affected advisory posture + mitigation hints."""
    status = build_advisory_status(current_version=PACK_VERSION)
    report.advisory_status = status
    report.environment["advisory_current_version"] = str(
        status.get("current_version", "")
    )
    report.environment["advisory_affected"] = (
        "true" if bool(status.get("affected")) else "false"
    )
    report.environment["advisory_high_severity_affected"] = str(
        int(status.get("high_severity_affected") or 0)
    )

    if not status.get("affected"):
        report.add(
            SecurityCheckResult(
                name="vulnerability_advisories",
                severity=SecuritySeverity.PASS.value,
                message="No applicable security advisories for current version",
                category="advisory",
            )
        )
        return

    high_count = int(status.get("high_severity_affected") or 0)
    total_affected = len(
        [entry for entry in status.get("advisories", []) if entry.get("affected")]
    )
    mitigation = str(status.get("mitigation") or "").strip()
    remediation = (
        mitigation or "Upgrade to a non-affected version listed in advisory guidance."
    )

    if high_count > 0:
        report.add(
            SecurityCheckResult(
                name="vulnerability_advisories",
                severity=SecuritySeverity.WARN.value,
                message=(
                    f"Current version is affected by {total_affected} advisory(s), "
                    f"including {high_count} high-severity advisory(s)"
                ),
                category="advisory",
                remediation=remediation,
            )
        )
        return

    report.add(
        SecurityCheckResult(
            name="vulnerability_advisories",
            severity=SecuritySeverity.WARN.value,
            message=(
                f"Current version is affected by {total_affected} non-high-severity advisory(s)"
            ),
            category="advisory",
            remediation=remediation,
        )
    )


# ---------------------------------------------------------------------------
# Security checks — S32 Connector security posture
# ---------------------------------------------------------------------------


def check_connector_security_posture(report: SecurityReport) -> None:
    """S32: Check connector security posture for internet-exposed deployments."""
    posture = evaluate_connector_allowlist_posture(os.environ)
    active_markers = posture["active_markers"]
    active_platforms = posture["active_platforms"]
    unguarded_platforms = posture["unguarded_platforms"]
    configured_allowlists = posture["configured_allowlists"]
    recommended_allowlist_vars = posture["recommended_allowlist_vars"]

    # --- Connector activation presence ---
    if active_markers:
        report.add(
            SecurityCheckResult(
                name="s32_connector_tokens",
                severity=SecuritySeverity.PASS.value,
                message=f"{len(active_platforms)} connector platform(s) active",
                category="connector",
                detail=(
                    "Platforms: "
                    + ", ".join(active_platforms)
                    + " | markers: "
                    + ", ".join(active_markers)
                ),
            )
        )
    else:
        report.add(
            SecurityCheckResult(
                name="s32_connector_tokens",
                severity=SecuritySeverity.INFO.value,
                message="No connector tokens configured (connectors not enabled)",
                category="connector",
            )
        )

    # --- Allowlist coverage ---
    # CRITICAL: keep strict-profile escalation aligned with startup fail-closed.
    # If this drifts, doctor may show WARN while startup hard-fails (or vice versa).
    if active_platforms and unguarded_platforms:
        strict_profile = is_strict_connector_allowlist_profile(os.environ)
        severity = (
            SecuritySeverity.FAIL.value
            if strict_profile
            else SecuritySeverity.WARN.value
        )
        posture_hint = "public/hardened" if strict_profile else "non-strict"
        report.add(
            SecurityCheckResult(
                name="s32_allowlist_coverage",
                severity=severity,
                message=(
                    "Connector ingress active but allowlist coverage missing for: "
                    + ", ".join(unguarded_platforms)
                ),
                category="connector",
                detail=(
                    f"Profile posture={posture_hint}. "
                    "Without allowlists, connectors may accept messages from any user/channel."
                ),
                remediation=(
                    "Set platform allowlists before enabling internet-facing connector ingress. "
                    "Allowed vars: " + ", ".join(recommended_allowlist_vars)
                ),
            )
        )
    elif active_platforms:
        report.add(
            SecurityCheckResult(
                name="s32_allowlist_coverage",
                severity=SecuritySeverity.PASS.value,
                message=(
                    f"Allowlist coverage present for {len(active_platforms)} active connector platform(s)"
                ),
                category="connector",
                detail=(
                    "Configured allowlists: " + ", ".join(configured_allowlists)
                    if configured_allowlists
                    else "Configured allowlists: (none required for inactive platforms)"
                ),
            )
        )

    # --- Webhook signature verification posture ---
    wa_token = os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN", "").strip()
    wa_secret = os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET", "").strip()
    line_secret = os.environ.get("OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET", "").strip()
    line_token = os.environ.get(
        "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN", ""
    ).strip()

    if wa_token and not wa_secret:
        report.add(
            SecurityCheckResult(
                name="s32_whatsapp_sig_missing",
                severity=SecuritySeverity.WARN.value,
                message="WhatsApp access token set but app_secret missing — webhook signature verification disabled",
                category="connector",
                remediation="Set OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET for production webhook security.",
            )
        )
    if line_token and not line_secret:
        report.add(
            SecurityCheckResult(
                name="s32_line_sig_missing",
                severity=SecuritySeverity.WARN.value,
                message="LINE access token set but channel_secret missing — webhook signature verification disabled",
                category="connector",
                remediation="Set OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET for production webhook security.",
            )
        )

    # --- DM policy open warning ---
    dev_mode = os.environ.get("MOLTBOT_DEV_MODE", "").strip().lower()
    if dev_mode in ("1", "true", "yes", "on") and active_markers:
        report.add(
            SecurityCheckResult(
                name="s32_dev_mode_with_connectors",
                severity=SecuritySeverity.WARN.value,
                message="Dev mode enabled with active connectors — auth bypass risk",
                category="connector",
                remediation="Disable MOLTBOT_DEV_MODE when connectors are internet-exposed.",
            )
        )


# ---------------------------------------------------------------------------
# Guarded remediation — safe/local-only actions
# ---------------------------------------------------------------------------

SAFE_REMEDIATIONS = {
    "tighten_state_dir": "Set state directory permissions to owner-only (chmod 700/600)",
    "tighten_secrets_file": "Set secrets file permissions to owner-only (chmod 600)",
}


def apply_guarded_remediation(
    report: SecurityReport,
    action: str,
    *,
    dry_run: bool = True,
) -> bool:
    """
    Apply a safe, predefined remediation.

    Only allows predefined safe actions (permissions tightening).
    No external command execution. No arbitrary file mutation.

    Args:
        report: The security report to append results to.
        action: One of the SAFE_REMEDIATIONS keys.
        dry_run: If True, only report what would be done.

    Returns:
        True if remediation was applied (or would be applied in dry_run).
    """
    if action not in SAFE_REMEDIATIONS:
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.FAIL.value,
                message=f"Unknown remediation action: {action}",
                category="remediation",
            )
        )
        return False

    if platform.system() == "Windows":
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.SKIP.value,
                message=f"Remediation '{action}' not supported on Windows (use ACLs manually)",
                category="remediation",
            )
        )
        return False

    try:
        from .state_dir import get_state_dir

        state_dir = get_state_dir()
    except Exception:
        try:
            from services.state_dir import get_state_dir

            state_dir = get_state_dir()
        except Exception:
            state_dir = None

    if not state_dir:
        return False

    if action == "tighten_state_dir":
        target = state_dir
        target_mode = 0o700
    elif action == "tighten_secrets_file":
        target = os.path.join(state_dir, "secrets.json")
        target_mode = 0o600
    else:
        return False

    if not os.path.exists(target):
        return False

    if dry_run:
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.INFO.value,
                message=f"[DRY RUN] Would set {target} to {oct(target_mode)}",
                category="remediation",
            )
        )
        return True

    try:
        os.chmod(target, target_mode)
        report.remediation_applied.append(
            f"{action}: set {target} to {oct(target_mode)}"
        )
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.PASS.value,
                message=f"Applied: set {target} to {oct(target_mode)}",
                category="remediation",
            )
        )
        return True
    except Exception as e:
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.FAIL.value,
                message=f"Remediation failed: {e}",
                category="remediation",
            )
        )
        return False


# ---------------------------------------------------------------------------
# Security checks — Wave 2 Hardening (S35, S12, R77)
# ---------------------------------------------------------------------------


def check_hardening_wave2(report: SecurityReport) -> None:
    """Verify Security Hardening Wave 2 status."""

    # 1. S35 Transform Isolation
    try:
        from .constrained_transforms import (
            TransformExecutorUnavailable,
            get_transform_executor,
        )
        from .transform_common import is_transforms_enabled
        from .transform_runner import TransformProcessRunner

        if not is_transforms_enabled():
            report.add(
                SecurityCheckResult(
                    name="s35_isolation",
                    severity=SecuritySeverity.SKIP.value,
                    message="Transforms disabled (feature flag off)",
                    category="wave2",
                )
            )
        else:
            executor = get_transform_executor()
            if isinstance(executor, TransformProcessRunner):
                report.add(
                    SecurityCheckResult(
                        name="s35_isolation",
                        severity=SecuritySeverity.PASS.value,
                        message="S35: Process isolation active",
                        category="wave2",
                    )
                )
            else:
                report.add(
                    SecurityCheckResult(
                        name="s35_isolation",
                        severity=SecuritySeverity.FAIL.value,
                        message="S35: Process isolation NOT active (using thread/unsafe executor)",
                        category="wave2",
                        detail=f"Current executor: {type(executor)}",
                        remediation="Ensure TransformProcessRunner is used.",
                    )
                )

    except TransformExecutorUnavailable as e:
        report.add(
            SecurityCheckResult(
                name="s35_isolation",
                severity=SecuritySeverity.FAIL.value,
                message="S35: Process isolation unavailable; transforms disabled for safety",
                category="wave2",
                detail=str(e),
                remediation="Restore services.transform_runner and its dependencies.",
            )
        )
    except ImportError:
        report.add(
            SecurityCheckResult(
                name="s35_isolation",
                severity=SecuritySeverity.FAIL.value,
                message="S35: Modules not importable",
                category="wave2",
            )
        )
    except RuntimeError as e:
        report.add(
            SecurityCheckResult(
                name="s35_isolation",
                severity=SecuritySeverity.FAIL.value,
                message="S35: Process isolation check failed at runtime",
                category="wave2",
                detail=str(e),
                remediation="Inspect transform runner initialization and environment dependencies.",
            )
        )

    # 2. S12 Tooling (Opt-in)
    try:
        from .tool_runner import is_tools_enabled

        if is_tools_enabled():
            report.add(
                SecurityCheckResult(
                    name="s12_tooling",
                    severity=SecuritySeverity.WARN.value,
                    message="S12: External tooling ENABLED (admin-only)",
                    category="wave2",
                    detail="Ensure tools_allowlist.json is strict.",
                )
            )
        else:
            report.add(
                SecurityCheckResult(
                    name="s12_tooling",
                    severity=SecuritySeverity.PASS.value,
                    message="S12: External tooling disabled (safe default)",
                    category="wave2",
                )
            )
    except ImportError:
        pass

    # 3. R77 Integrity (Existence Check)
    try:
        from .integrity import load_verified

        report.add(
            SecurityCheckResult(
                name="r77_integrity",
                severity=SecuritySeverity.PASS.value,
                message="R77: Integrity module loaded",
                category="wave2",
            )
        )
    except ImportError:
        report.add(
            SecurityCheckResult(
                name="r77_integrity",
                severity=SecuritySeverity.FAIL.value,
                message="R77: Integrity module missing",
                category="wave2",
            )
        )


# ---------------------------------------------------------------------------
# Security checks — S45 exposure posture parity
# ---------------------------------------------------------------------------


def check_s45_exposure_posture(report: SecurityReport) -> None:
    """S45 parity: mirror SecurityGate exposure logic as doctor violations."""
    import sys as _sys

    # Mirror SecurityGate._check_network_exposure()
    is_exposed = "--listen" in _sys.argv

    try:
        from .access_control import is_any_token_configured
    except ImportError:
        try:
            from services.access_control import is_any_token_configured  # type: ignore
        except ImportError:
            return  # cannot evaluate

    auth_ready = is_any_token_configured()

    if is_exposed and not auth_ready:
        # Check for dangerous override
        try:
            from .runtime_config import get_config
        except ImportError:
            from services.runtime_config import get_config  # type: ignore

        config = get_config()
        if config.security_dangerous_bind_override:
            report.add(
                SecurityCheckResult(
                    name="s45_dangerous_override",
                    severity=SecuritySeverity.WARN.value,
                    message="Server exposed without auth but dangerous override is active",
                    category="exposure",
                    detail="OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE=1",
                    remediation="Remove override and configure authentication tokens.",
                )
            )
        else:
            report.add(
                SecurityCheckResult(
                    name="s45_exposed_no_auth",
                    severity=SecuritySeverity.FAIL.value,
                    message="Server exposed (--listen) without any authentication token",
                    category="exposure",
                    remediation="Set OPENCLAW_ADMIN_TOKEN or OPENCLAW_OBSERVABILITY_TOKEN.",
                )
            )
    elif not auth_ready:
        # Loopback + no auth — warn in hardened mode
        try:
            from .runtime_profile import is_hardened_mode
        except ImportError:
            try:
                from services.runtime_profile import is_hardened_mode  # type: ignore
            except ImportError:
                return

        try:
            from .access_control import is_auth_configured
        except ImportError:
            from services.access_control import is_auth_configured  # type: ignore

        if is_hardened_mode() and not is_auth_configured():
            report.add(
                SecurityCheckResult(
                    name="s45_hardened_loopback_no_admin",
                    severity=SecuritySeverity.WARN.value,
                    message="HARDENED profile requires admin auth even on loopback",
                    category="exposure",
                    remediation="Set OPENCLAW_ADMIN_TOKEN for hardened deployments.",
                )
            )
    else:
        report.add(
            SecurityCheckResult(
                name="s45_exposure_posture",
                severity=SecuritySeverity.PASS.value,
                message="S45 exposure posture OK",
                category="exposure",
            )
        )


# ---------------------------------------------------------------------------
# Security checks — S66 runtime guardrails diagnostics
# ---------------------------------------------------------------------------


def check_runtime_guardrails(report: SecurityReport) -> None:
    """S66: Surface centralized runtime guardrail diagnostics."""
    try:
        from .runtime_guardrails import get_runtime_guardrails_snapshot
    except ImportError:
        try:
            from services.runtime_guardrails import (  # type: ignore
                get_runtime_guardrails_snapshot,
            )
        except ImportError:
            report.add(
                SecurityCheckResult(
                    name="s66_runtime_guardrails",
                    severity=SecuritySeverity.SKIP.value,
                    message="S66 runtime guardrails module unavailable",
                    category="runtime",
                )
            )
            return

    snapshot = get_runtime_guardrails_snapshot()
    report.environment["runtime_guardrails_status"] = str(snapshot.get("status", "ok"))
    report.environment["runtime_guardrails_code"] = str(snapshot.get("code", ""))
    report.environment["runtime_guardrails_violation_count"] = str(
        len(snapshot.get("violations", []))
    )

    if snapshot.get("status") == "ok":
        report.add(
            SecurityCheckResult(
                name="s66_runtime_guardrails",
                severity=SecuritySeverity.PASS.value,
                message="S66 runtime guardrails diagnostics OK",
                category="runtime",
            )
        )
        return

    violations = snapshot.get("violations", [])
    first = violations[0] if violations else {}
    report.add(
        SecurityCheckResult(
            name="s66_runtime_guardrails",
            severity=SecuritySeverity.WARN.value,
            message="S66 runtime guardrails degraded (invalid/clamped ENV values)",
            category="runtime",
            detail=(
                f"code={snapshot.get('code')} "
                f"path={first.get('path','')} "
                f"violation={first.get('code','')}"
            ).strip(),
            remediation="Fix invalid OPENCLAW guardrail environment values or remove overrides.",
        )
    )


def check_csrf_no_origin_override(report: SecurityReport) -> None:
    """S68: Surface localhost no-origin CSRF override posture explicitly."""
    raw = os.environ.get("OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN", "")
    enabled = raw.strip().lower() in {"1", "true", "yes", "on"}
    report.environment["csrf_no_origin_override"] = "enabled" if enabled else "off"

    if enabled:
        report.add(
            SecurityCheckResult(
                name="csrf_no_origin_override",
                severity=SecuritySeverity.WARN.value,
                message=(
                    "OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN is enabled; "
                    "requests without Origin/Sec-Fetch-Site are allowed in localhost convenience mode"
                ),
                category="endpoint",
                remediation=(
                    "Unset OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN unless CLI/no-origin clients are required."
                ),
            )
        )
        return

    report.add(
        SecurityCheckResult(
            name="csrf_no_origin_override",
            severity=SecuritySeverity.PASS.value,
            message="No-origin CSRF override is disabled (strict default active)",
            category="endpoint",
        )
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_security_doctor(
    *,
    remediate: bool = False,
    dry_run: bool = True,
) -> SecurityReport:
    """
    Run all security diagnostic checks and return a report.

    Args:
        remediate: If True, apply safe remediations after scanning.
        dry_run: If True (default), only report what would be remediated.
    """
    report = SecurityReport()
    pack_root = _get_pack_root()

    report.environment["pack_root"] = str(pack_root)
    report.environment["scan_mode"] = (
        "read-only" if not remediate else ("dry-run" if dry_run else "remediate")
    )

    # Run all checks
    check_s45_exposure_posture(report)  # S45 parity (first — sets high_risk_mode)
    check_endpoint_exposure(report)
    check_public_shared_surface_boundary(report)  # S69 shared-surface boundary
    check_token_boundaries(report)
    check_ssrf_posture(report)
    check_state_dir_permissions(report)
    check_redaction_drift(report)
    check_comfyui_runtime(report)
    check_runtime_guardrails(report)  # S66
    check_csrf_no_origin_override(report)  # S68
    check_feature_flags(report)
    check_vulnerability_advisories(report)  # S48
    check_api_key_posture(report)
    check_connector_security_posture(report)  # S32
    check_hardening_wave2(report)  # Wave 2

    # Optional guarded remediation
    if remediate:
        # Identify failing checks that have safe remediations
        for check in report.checks:
            if check.severity == SecuritySeverity.FAIL.value:
                if "state_dir" in check.name and "world" in check.message.lower():
                    if "secret" in check.name:
                        apply_guarded_remediation(
                            report, "tighten_secrets_file", dry_run=dry_run
                        )
                    else:
                        apply_guarded_remediation(
                            report, "tighten_state_dir", dry_run=dry_run
                        )

    report.build_summary()
    return report


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for security doctor."""
    import argparse

    parser = argparse.ArgumentParser(
        description="OpenClaw Security Doctor — security posture diagnostics"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human-readable text",
    )
    parser.add_argument(
        "--remediate",
        action="store_true",
        help="Apply safe remediations (permissions tightening only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Only report what would be remediated (default: True)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply remediations (requires --remediate)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    report = run_security_doctor(remediate=args.remediate, dry_run=dry_run)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_human())

    sys.exit(1 if report.has_failures else 0)


if __name__ == "__main__":
    main()
