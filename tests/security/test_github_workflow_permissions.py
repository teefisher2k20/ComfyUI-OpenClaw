import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
EXPECTED_PERMISSION_BLOCKS = {
    "ci.yml": "permissions:\n  contents: read\n",
    "codeql.yml": (
        "permissions:\n"
        "  actions: read\n"
        "  contents: read\n"
        "  security-events: write\n"
    ),
    "pre-commit.yml": "permissions:\n  contents: read\n",
    "secret-scan.yml": "permissions:\n  contents: read\n",
}


class TestGitHubWorkflowPermissions(unittest.TestCase):

    def test_affected_workflows_declare_top_level_permissions(self):
        for workflow_name, permissions_block in EXPECTED_PERMISSION_BLOCKS.items():
            with self.subTest(workflow=workflow_name):
                workflow_text = (WORKFLOW_ROOT / workflow_name).read_text(
                    encoding="utf-8"
                )
                jobs_index = workflow_text.find("\njobs:")
                self.assertGreater(
                    jobs_index,
                    0,
                    f"{workflow_name} must contain a jobs section",
                )

                permissions_index = workflow_text.find(permissions_block)
                self.assertGreaterEqual(
                    permissions_index,
                    0,
                    f"{workflow_name} must declare explicit top-level permissions",
                )
                self.assertLess(
                    permissions_index,
                    jobs_index,
                    f"{workflow_name} permissions block must stay top-level before jobs",
                )


if __name__ == "__main__":
    unittest.main()
