import unittest
from unittest.mock import patch

from services.security_doctor import SecurityReport, check_vulnerability_advisories


class TestS48SecurityDoctorAdvisorySurface(unittest.TestCase):
    def test_unaffected_version_reports_pass(self):
        report = SecurityReport()
        with patch(
            "services.security_doctor_impl.build_advisory_status",
            return_value={
                "current_version": "0.3.0",
                "affected": False,
                "high_severity_affected": 0,
                "mitigation": "",
                "advisories": [],
            },
        ):
            check_vulnerability_advisories(report)

        self.assertEqual(report.advisory_status["affected"], False)
        check = next(c for c in report.checks if c.name == "vulnerability_advisories")
        self.assertEqual(check.severity, "pass")

    def test_high_severity_affected_reports_warn_and_mitigation(self):
        report = SecurityReport()
        with patch(
            "services.security_doctor_impl.build_advisory_status",
            return_value={
                "current_version": "0.3.2",
                "affected": True,
                "high_severity_affected": 1,
                "mitigation": "Upgrade to >=0.3.4.",
                "advisories": [
                    {
                        "id": "OPENCLAW-2026-0001",
                        "severity": "high",
                        "affected": True,
                    }
                ],
            },
        ):
            check_vulnerability_advisories(report)

        self.assertEqual(report.advisory_status["affected"], True)
        self.assertEqual(report.environment["advisory_affected"], "true")
        check = next(c for c in report.checks if c.name == "vulnerability_advisories")
        self.assertEqual(check.severity, "warn")
        self.assertIn("Upgrade to >=0.3.4.", check.remediation)


if __name__ == "__main__":
    unittest.main()

