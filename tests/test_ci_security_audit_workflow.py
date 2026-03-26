import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _extract_security_audit_job(text: str) -> str:
    match = re.search(
        r"(?ms)^  security-audit:\n(?P<body>.*?)(?=^  [a-z0-9][a-z0-9-]*:\n|\Z)",
        text,
    )
    if not match:
        raise AssertionError("security-audit job missing from .github/workflows/ci.yml")
    return match.group("body")


class SecurityAuditWorkflowRegressionTests(unittest.TestCase):
    def test_backend_audit_is_scoped_to_declared_requirements(self):
        text = CI_WORKFLOW.read_text(encoding="utf-8")
        job = _extract_security_audit_job(text)

        self.assertIn("python -m pip install -r requirements.txt", job)
        self.assertIn("pip-audit -r requirements.txt", job)

        bare_invocations = [
            line.strip() for line in job.splitlines() if line.strip() == "pip-audit"
        ]
        self.assertEqual(
            bare_invocations,
            [],
            "security-audit job must not run env-wide bare `pip-audit`",
        )


if __name__ == "__main__":
    unittest.main()
