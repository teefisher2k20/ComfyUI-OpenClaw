import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
PRE_PUSH = ROOT / "scripts" / "pre_push_checks.sh"
FULL_TESTS_LINUX = ROOT / "scripts" / "run_full_tests_linux.sh"
FULL_TESTS_WINDOWS = ROOT / "scripts" / "run_full_tests_windows.ps1"
PREFLIGHT = ROOT / "scripts" / "preflight_check.py"
PYPROJECT = ROOT / "pyproject.toml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _parse_required_python_packages():
    module = ast.parse(PREFLIGHT.read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "REQUIRED_PYTHON_PACKAGES":
                return ast.literal_eval(node.value)
    raise AssertionError("REQUIRED_PYTHON_PACKAGES missing from scripts/preflight_check.py")


class DependencyParityContractTests(unittest.TestCase):
    def test_requirements_stays_aligned_with_declared_runtime_dependencies(self):
        requirements = REQUIREMENTS.read_text(encoding="utf-8")
        pyproject = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn("cryptography>=41.0", requirements)
        self.assertIn("defusedxml>=0.7.1", requirements)
        self.assertIn('dependencies = ["cryptography>=41.0", "defusedxml>=0.7.1"]', pyproject)

    def test_preflight_declares_all_essential_runtime_packages(self):
        required = _parse_required_python_packages()
        self.assertIn(("cryptography", "41.0"), required)
        self.assertIn(("defusedxml", "0.7.1"), required)

    def test_local_acceptance_bootstraps_install_defusedxml(self):
        self.assertIn("import defusedxml", PRE_PUSH.read_text(encoding="utf-8"))
        self.assertIn("pip install defusedxml", FULL_TESTS_WINDOWS.read_text(encoding="utf-8"))
        self.assertIn("import defusedxml", FULL_TESTS_LINUX.read_text(encoding="utf-8"))

    def test_frontend_e2e_preflight_uses_requirements_contract(self):
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python -m pip install -r requirements.txt", workflow)


if __name__ == "__main__":
    unittest.main()
