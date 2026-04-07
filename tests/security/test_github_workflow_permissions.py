import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
AFFECTED_WORKFLOWS = (
    "ci.yml",
    "pre-commit.yml",
    "secret-scan.yml",
)


class TestGitHubWorkflowPermissions(unittest.TestCase):

    def test_affected_workflows_declare_top_level_permissions(self):
        for workflow_name in AFFECTED_WORKFLOWS:
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

                permissions_block = "permissions:\n  contents: read\n"
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
