import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEQL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "codeql.yml"


class TestCodeqlWorkflowContract(unittest.TestCase):
    def test_workflow_exists(self):
        self.assertTrue(
            CODEQL_WORKFLOW.exists(),
            "codeql.yml must exist so GitHub security scanning stays versioned in-repo",
        )

    def test_workflow_declares_expected_triggers(self):
        workflow = CODEQL_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("push:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("- main", workflow)

    def test_workflow_declares_minimum_permissions(self):
        workflow = CODEQL_WORKFLOW.read_text(encoding="utf-8")
        permissions_block = (
            "permissions:\n"
            "  actions: read\n"
            "  contents: read\n"
            "  security-events: write\n"
        )
        self.assertIn(permissions_block, workflow)

    def test_workflow_covers_repo_languages(self):
        workflow = CODEQL_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- language: actions", workflow)
        self.assertIn("- language: javascript-typescript", workflow)
        self.assertIn("- language: python", workflow)
        self.assertIn("github/codeql-action/init@v4", workflow)
        self.assertIn("github/codeql-action/analyze@v4", workflow)


if __name__ == "__main__":
    unittest.main()
