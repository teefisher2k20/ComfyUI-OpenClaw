import unittest

from services.security_advisories import (
    evaluate_advisories,
    is_version_in_range,
    parse_semver,
)


class TestS48SecurityAdvisories(unittest.TestCase):
    def test_parse_semver_accepts_prerelease_suffix(self):
        self.assertEqual(parse_semver("1.2.3-beta.1"), (1, 2, 3))

    def test_semver_range_match_basic(self):
        self.assertTrue(is_version_in_range("0.2.3", ">=0.2.0,<0.2.5"))
        self.assertFalse(is_version_in_range("0.2.5", ">=0.2.0,<0.2.5"))

    def test_semver_range_match_exact(self):
        self.assertTrue(is_version_in_range("1.0.0", "==1.0.0"))
        self.assertFalse(is_version_in_range("1.0.1", "==1.0.0"))

    def test_evaluate_advisories_affected_and_mitigation(self):
        result = evaluate_advisories(
            current_version="0.3.2",
            advisories=[
                {
                    "id": "OPENCLAW-2026-0001",
                    "severity": "high",
                    "affected_range": ">=0.3.0,<0.3.4",
                    "mitigation": "Upgrade to >=0.3.4 immediately.",
                    "summary": "Test high severity advisory",
                },
                {
                    "id": "OPENCLAW-2026-0002",
                    "severity": "medium",
                    "affected_range": ">=0.1.0,<0.2.0",
                    "mitigation": "Upgrade to >=0.2.0.",
                    "summary": "Old medium advisory",
                },
            ],
        )
        self.assertTrue(result["affected"])
        self.assertEqual(result["high_severity_affected"], 1)
        self.assertEqual(result["mitigation"], "Upgrade to >=0.3.4 immediately.")
        affected_ids = {entry["id"] for entry in result["advisories"] if entry["affected"]}
        self.assertEqual(affected_ids, {"OPENCLAW-2026-0001"})

    def test_evaluate_advisories_unaffected(self):
        result = evaluate_advisories(
            current_version="0.4.0",
            advisories=[
                {
                    "id": "OPENCLAW-2026-0003",
                    "severity": "critical",
                    "affected_range": ">=0.3.0,<0.3.9",
                    "mitigation": "Upgrade to >=0.3.9",
                }
            ],
        )
        self.assertFalse(result["affected"])
        self.assertEqual(result["high_severity_affected"], 0)
        self.assertEqual(result["mitigation"], "")


if __name__ == "__main__":
    unittest.main()

