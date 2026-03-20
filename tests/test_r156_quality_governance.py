import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_quality_governance.py"


class TestR156QualityGovernance(unittest.TestCase):
    def _run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )

    def test_repo_governance_baseline_passes(self):
        result = self._run_script()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("GOVERNANCE-PASS", result.stdout)

    def test_missing_fail_under_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pyproject = tmp / "pyproject.toml"
            pyproject.write_text(
                textwrap.dedent(
                    """
                    [tool.coverage.report]
                    show_missing = true
                    skip_covered = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            gate = tmp / "run_adversarial_gate.py"
            gate.write_text(
                "SMOKE_MUTATION_THRESHOLD = 20.0\nEXTENDED_MUTATION_THRESHOLD = 80.0\n",
                encoding="utf-8",
            )

            sop = tmp / "TEST_SOP.md"
            sop.write_text(
                textwrap.dedent(
                    """
                    R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)
                    global score threshold (`>= 80%` unless explicitly overridden)
                    coverage governance check (`scripts/verify_quality_governance.py`)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            allowlist = tmp / "mutation_survivor_allowlist.json"
            allowlist.write_text('{"entries":[]}\n', encoding="utf-8")

            result = self._run_script(
                "--pyproject",
                str(pyproject),
                "--adversarial-gate",
                str(gate),
                "--test-sop",
                str(sop),
                "--mutation-survivor-allowlist",
                str(allowlist),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing coverage fail_under", result.stdout)
