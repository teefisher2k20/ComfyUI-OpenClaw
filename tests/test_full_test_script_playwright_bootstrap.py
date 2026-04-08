import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINUX_SCRIPT = ROOT / "scripts" / "run_full_tests_linux.sh"
WINDOWS_SCRIPT = ROOT / "scripts" / "run_full_tests_windows.ps1"


class TestFullTestScriptPlaywrightBootstrap(unittest.TestCase):
    def test_linux_full_gate_bootstraps_frontend_deps_and_browsers(self):
        content = LINUX_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("ensure_npm_deps", content)
        self.assertIn("npm install", content)
        self.assertIn("OPENCLAW_PLAYWRIGHT_INSTALL=1", content)
        self.assertIn("OPENCLAW_PLAYWRIGHT_BROWSERS=chromium", content)

    def test_windows_full_gate_bootstraps_frontend_deps_and_browsers(self):
        content = WINDOWS_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Ensure-NpmDeps", content)
        self.assertIn("npm install", content)
        self.assertIn('$env:OPENCLAW_PLAYWRIGHT_INSTALL = "1"', content)
        self.assertIn('$env:OPENCLAW_PLAYWRIGHT_BROWSERS = "chromium"', content)

    def test_windows_precommit_snapshot_check_accepts_clean_diff_state(self):
        content = WINDOWS_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('[AllowEmptyString()][string]$BeforeWorktree = ""', content)
        self.assertIn('[AllowEmptyString()][string]$BeforeIndex = ""', content)
        self.assertNotIn(
            "[Parameter(Mandatory = $true)][string]$BeforeIndex",
            content,
        )
