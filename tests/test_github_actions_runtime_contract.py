import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

DEPRECATED_ACTION_PATTERNS = {
    "actions/checkout@v4": re.compile(r"\bactions/checkout@v4\b"),
    "actions/setup-python@v5": re.compile(r"\bactions/setup-python@v5\b"),
    "actions/setup-node@v4": re.compile(r"\bactions/setup-node@v4\b"),
    "actions/upload-artifact@v4": re.compile(r"\bactions/upload-artifact@v4\b"),
    "Comfy-Org/publish-node-action@v1": re.compile(
        r"\bComfy-Org/publish-node-action@v1\b"
    ),
}

EXPECTED_ACTION_PATTERNS = {
    "actions/checkout@v5": re.compile(r"\bactions/checkout@v5\b"),
    "actions/setup-python@v6": re.compile(r"\bactions/setup-python@v6\b"),
    "actions/setup-node@v5": re.compile(r"\bactions/setup-node@v5\b"),
    "actions/upload-artifact@v6": re.compile(r"\bactions/upload-artifact@v6\b"),
}


class GitHubActionsRuntimeContractTests(unittest.TestCase):
    def test_workflows_do_not_reference_deprecated_node20_action_runtimes(self):
        for workflow_path in sorted(WORKFLOW_DIR.glob("*.yml")):
            text = workflow_path.read_text(encoding="utf-8")
            for action_label, pattern in DEPRECATED_ACTION_PATTERNS.items():
                self.assertIsNone(
                    pattern.search(text),
                    f"{workflow_path.name} still references deprecated action {action_label}",
                )

    def test_core_workflows_use_upgraded_action_versions(self):
        expected_files = {
            "ci.yml": {
                "actions/checkout@v5",
                "actions/setup-python@v6",
                "actions/setup-node@v5",
                "actions/upload-artifact@v6",
            },
            "pre-commit.yml": {
                "actions/checkout@v5",
                "actions/setup-python@v6",
            },
            "secret-scan.yml": {
                "actions/checkout@v5",
                "actions/setup-python@v6",
            },
            "publish.yml": {
                "actions/checkout@v5",
                "actions/setup-python@v6",
            },
        }

        for workflow_name, required_actions in expected_files.items():
            text = (WORKFLOW_DIR / workflow_name).read_text(encoding="utf-8")
            for action_label in required_actions:
                self.assertRegex(
                    text,
                    EXPECTED_ACTION_PATTERNS[action_label],
                    f"{workflow_name} must pin {action_label}",
                )

    def test_publish_workflow_uses_direct_cli_publish_flow(self):
        text = (WORKFLOW_DIR / "publish.yml").read_text(encoding="utf-8")
        self.assertIn("pip install comfy-cli", text)
        self.assertIn(
            'comfy --skip-prompt --no-enable-telemetry env comfy node publish --token "${REGISTRY_ACCESS_TOKEN}"',
            text,
        )


if __name__ == "__main__":
    unittest.main()
