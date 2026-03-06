"""
S30 Posture Report Contract Tests.

Validates schema_version, posture, high_risk_mode, violations[] envelope,
and S45 startup gate parity.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.security_doctor import (
    VIOLATION_CODE_MAP,
    SecurityCheckResult,
    SecurityReport,
    SecuritySeverity,
)

# ---------------------------------------------------------------------------
# Required contract keys for schema drift guard
# ---------------------------------------------------------------------------
_REQUIRED_REPORT_KEYS = {
    "environment",
    "checks",
    "summary",
    "risk_score",
    "remediation_applied",
    "schema_version",
    "posture",
    "high_risk_mode",
    "high_risk_reasons",
    "violations",
    "advisory_status",
}


class TestReportEnvelopeContract(unittest.TestCase):
    """WP4-1: Verify report envelope includes all required fields."""

    def test_report_envelope_contract(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="admin_token_set",
                severity=SecuritySeverity.PASS.value,
                message="Admin token configured",
                category="endpoint",
            )
        )
        d = report.to_dict()
        self.assertTrue(
            _REQUIRED_REPORT_KEYS.issubset(d.keys()),
            f"Missing keys: {_REQUIRED_REPORT_KEYS - d.keys()}",
        )
        self.assertEqual(d["schema_version"], "1.0")
        self.assertIsInstance(d["advisory_status"], dict)

    def test_legacy_fields_preserved(self):
        report = SecurityReport()
        d = report.to_dict()
        for key in (
            "environment",
            "checks",
            "summary",
            "risk_score",
            "remediation_applied",
        ):
            self.assertIn(key, d, f"Legacy field '{key}' missing from report")


class TestPostureDeterminism(unittest.TestCase):
    """WP4-2: posture must be deterministic across severity mixes."""

    def test_all_pass_posture(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="admin_token_set",
                severity="pass",
                message="ok",
                category="endpoint",
            )
        )
        report.add(
            SecurityCheckResult(
                name="ssrf_posture", severity="pass", message="ok", category="ssrf"
            )
        )
        d = report.to_dict()
        self.assertEqual(d["posture"], "pass")

    def test_warn_only_posture_pass(self):
        """WARN without FAIL should still be posture=pass."""
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="endpoint_exposure",
                severity="warn",
                message="no tokens",
                category="endpoint",
            )
        )
        d = report.to_dict()
        self.assertEqual(d["posture"], "pass")

    def test_fail_posture(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="token_reuse",
                severity="fail",
                message="tokens identical",
                category="token",
            )
        )
        d = report.to_dict()
        self.assertEqual(d["posture"], "fail")

    def test_mixed_pass_warn_fail(self):
        """Mixed severities: any fail → posture=fail."""
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="admin_token_set",
                severity="pass",
                message="ok",
                category="endpoint",
            )
        )
        report.add(
            SecurityCheckResult(
                name="endpoint_exposure",
                severity="warn",
                message="no tokens",
                category="endpoint",
            )
        )
        report.add(
            SecurityCheckResult(
                name="token_reuse", severity="fail", message="reuse", category="token"
            )
        )
        d = report.to_dict()
        self.assertEqual(d["posture"], "fail")

    def test_unmapped_fail_posture_still_fail(self):
        """HIGH regression: unmapped fail check must still force posture='fail'.

        Previously, posture was computed only from violations (which excludes
        unmapped checks), causing a false-pass for unmapped fail checks.
        """
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="unknown_fail",
                severity="fail",
                message="This check has no code mapping",
                category="misc",
            )
        )
        d = report.to_dict()
        self.assertEqual(
            d["violations"], [], "unmapped check should not appear in violations"
        )
        self.assertEqual(
            d["posture"], "fail", "posture must be fail even for unmapped fail checks"
        )


class TestViolationsStableCodes(unittest.TestCase):
    """WP4-3: Mapped violations emit stable codes."""

    def test_known_fail_mapped(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="token_reuse",
                severity="fail",
                message="same",
                category="token",
                remediation="Use distinct tokens.",
            )
        )
        d = report.to_dict()
        violations = d["violations"]
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v["code"], "SEC-TK-001")
        self.assertEqual(v["severity"], "fail")
        self.assertEqual(v["check"], "token_reuse")
        self.assertIn("remediation", v)

    def test_warn_mapped(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="endpoint_exposure",
                severity="warn",
                message="no tokens",
                category="endpoint",
            )
        )
        d = report.to_dict()
        violations = d["violations"]
        codes = [v["code"] for v in violations]
        self.assertIn("SEC-EP-001", codes)

    def test_pass_not_in_violations(self):
        """PASS checks must not appear in violations."""
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="admin_token_set",
                severity="pass",
                message="ok",
                category="endpoint",
            )
        )
        d = report.to_dict()
        self.assertEqual(d["violations"], [])

    def test_unmapped_warn_excluded(self):
        """Checks with no code mapping are excluded from violations."""
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="some_unknown_check",
                severity="warn",
                message="unknown",
                category="misc",
            )
        )
        d = report.to_dict()
        self.assertEqual(d["violations"], [])

    def test_violation_entry_shape(self):
        """Each violation has required keys: code, severity, check, message."""
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="callback_wildcard",
                severity="fail",
                message="wildcard",
                category="ssrf",
            )
        )
        d = report.to_dict()
        v = d["violations"][0]
        for key in ("code", "severity", "check", "message"):
            self.assertIn(key, v)

    def test_advisory_warn_is_mapped(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="vulnerability_advisories",
                severity="warn",
                message="affected by high severity advisory",
                category="advisory",
            )
        )
        d = report.to_dict()
        self.assertIn("SEC-VA-001", [v["code"] for v in d["violations"]])


class TestHighRiskMode(unittest.TestCase):
    """WP4-4: high_risk_mode triggers on S45 + feature flag codes."""

    def test_not_high_risk_clean(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="admin_token_set",
                severity="pass",
                message="ok",
                category="endpoint",
            )
        )
        d = report.to_dict()
        self.assertFalse(d["high_risk_mode"])
        self.assertEqual(d["high_risk_reasons"], [])

    def test_high_risk_s45_exposed(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="s45_exposed_no_auth",
                severity="fail",
                message="exposed",
                category="exposure",
            )
        )
        d = report.to_dict()
        self.assertTrue(d["high_risk_mode"])
        self.assertIn("SEC-S45-001", d["high_risk_reasons"])

    def test_high_risk_dangerous_override(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="s45_dangerous_override",
                severity="warn",
                message="override active",
                category="exposure",
            )
        )
        d = report.to_dict()
        self.assertTrue(d["high_risk_mode"])
        self.assertIn("SEC-S45-002", d["high_risk_reasons"])

    def test_high_risk_feature_flags(self):
        report = SecurityReport()
        report.add(
            SecurityCheckResult(
                name="high_risk_flags",
                severity="warn",
                message="1 flag enabled",
                category="feature_flags",
            )
        )
        d = report.to_dict()
        self.assertTrue(d["high_risk_mode"])
        self.assertIn("SEC-FF-001", d["high_risk_reasons"])


class TestSchemaDriftGuard(unittest.TestCase):
    """WP4-5: Regression guard — required fields never disappear."""

    def test_schema_drift(self):
        report = SecurityReport()
        d = report.to_dict()
        missing = _REQUIRED_REPORT_KEYS - set(d.keys())
        self.assertEqual(
            missing,
            set(),
            f"Schema drift: required fields missing from report: {missing}",
        )


class TestS45Parity(unittest.TestCase):
    """WP4-6: Verify doctor S45 check produces violations consistent with SecurityGate."""

    def test_exposed_no_auth_parity(self):
        """Exposed + no auth → doctor emits SEC-S45-001 (matching gate FATAL)."""
        from services.security_doctor import check_s45_exposure_posture

        report = SecurityReport()

        mock_access = MagicMock()
        mock_access.is_any_token_configured = MagicMock(return_value=False)
        mock_config_mod = MagicMock()
        mock_config = MagicMock()
        mock_config.security_dangerous_bind_override = False
        mock_config_mod.get_config = MagicMock(return_value=mock_config)

        with (
            patch("sys.argv", ["main.py", "--listen"]),
            patch.dict(
                "sys.modules",
                {
                    "services.access_control": mock_access,
                    "services.runtime_config": mock_config_mod,
                },
            ),
        ):
            check_s45_exposure_posture(report)

        names = [c.name for c in report.checks]
        self.assertIn("s45_exposed_no_auth", names)
        d = report.to_dict()
        codes = [v["code"] for v in d["violations"]]
        self.assertIn("SEC-S45-001", codes)
        self.assertEqual(d["posture"], "fail")

    def test_exposed_with_override_parity(self):
        """Exposed + override → doctor emits SEC-S45-002 (matching gate WARNING)."""
        from services.security_doctor import check_s45_exposure_posture

        report = SecurityReport()

        mock_access = MagicMock()
        mock_access.is_any_token_configured = MagicMock(return_value=False)
        mock_config_mod = MagicMock()
        mock_config = MagicMock()
        mock_config.security_dangerous_bind_override = True
        mock_config_mod.get_config = MagicMock(return_value=mock_config)

        with (
            patch("sys.argv", ["main.py", "--listen"]),
            patch.dict(
                "sys.modules",
                {
                    "services.access_control": mock_access,
                    "services.runtime_config": mock_config_mod,
                },
            ),
        ):
            check_s45_exposure_posture(report)

        names = [c.name for c in report.checks]
        self.assertIn("s45_dangerous_override", names)
        d = report.to_dict()
        codes = [v["code"] for v in d["violations"]]
        self.assertIn("SEC-S45-002", codes)
        self.assertTrue(d["high_risk_mode"])

    def test_hardened_loopback_parity(self):
        """Loopback + hardened + no admin → doctor emits SEC-S45-003."""
        from services.security_doctor import check_s45_exposure_posture

        report = SecurityReport()

        mock_access = MagicMock()
        mock_access.is_any_token_configured = MagicMock(return_value=False)
        mock_access.is_auth_configured = MagicMock(return_value=False)
        mock_profile = MagicMock()
        mock_profile.is_hardened_mode = MagicMock(return_value=True)

        with (
            patch("sys.argv", ["main.py"]),
            patch.dict(
                "sys.modules",
                {
                    "services.access_control": mock_access,
                    "services.runtime_profile": mock_profile,
                },
            ),
        ):
            check_s45_exposure_posture(report)

        names = [c.name for c in report.checks]
        self.assertIn("s45_hardened_loopback_no_admin", names)
        d = report.to_dict()
        codes = [v["code"] for v in d["violations"]]
        self.assertIn("SEC-S45-003", codes)


if __name__ == "__main__":
    unittest.main()
