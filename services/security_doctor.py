"""
Security Doctor compatibility shim.

R130: module decomposition entrypoint.
Keep `services.security_doctor` imports stable while implementation is split into
report/check/remediation/runner slices.
"""

from .security_doctor_checks import (
    check_api_key_posture,
    check_comfyui_runtime,
    check_connector_security_posture,
    check_csrf_no_origin_override,
    check_endpoint_exposure,
    check_feature_flags,
    check_hardening_wave2,
    check_public_shared_surface_boundary,
    check_redaction_drift,
    check_runtime_guardrails,
    check_s45_exposure_posture,
    check_ssrf_posture,
    check_state_dir_permissions,
    check_token_boundaries,
    check_vulnerability_advisories,
)
from .security_doctor_remediation import apply_guarded_remediation
from .security_doctor_report import (
    VIOLATION_CODE_MAP,
    SecurityCheckResult,
    SecurityReport,
    SecuritySeverity,
)
from .security_doctor_runner import main, run_security_doctor

__all__ = [
    "SecuritySeverity",
    "SecurityCheckResult",
    "SecurityReport",
    "VIOLATION_CODE_MAP",
    "check_endpoint_exposure",
    "check_public_shared_surface_boundary",
    "check_csrf_no_origin_override",
    "check_token_boundaries",
    "check_ssrf_posture",
    "check_state_dir_permissions",
    "check_redaction_drift",
    "check_comfyui_runtime",
    "check_feature_flags",
    "check_api_key_posture",
    "check_vulnerability_advisories",
    "check_connector_security_posture",
    "check_hardening_wave2",
    "check_s45_exposure_posture",
    "check_runtime_guardrails",
    "apply_guarded_remediation",
    "run_security_doctor",
    "main",
]


if __name__ == "__main__":
    main()
