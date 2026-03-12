import importlib.util
import tempfile
import unittest
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "check_openapi_sync.py"
    spec = importlib.util.spec_from_file_location("openapi_sync_guard_mod", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load check_openapi_sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestOpenApiSyncGuard(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_should_validate_openapi_true_for_generated_spec(self):
        self.assertTrue(self.mod.should_validate_openapi(["docs/openapi.yaml"]))

    def test_should_validate_openapi_true_for_generator_source(self):
        self.assertTrue(
            self.mod.should_validate_openapi(["services/openapi_generation.py"])
        )

    def test_should_validate_openapi_false_for_unrelated_paths(self):
        self.assertFalse(self.mod.should_validate_openapi(["README.md"]))

    def test_validate_openapi_sync_passes_when_content_matches(self):
        with tempfile.TemporaryDirectory() as td:
            openapi_path = Path(td) / "openapi.yaml"
            contract_path = Path(td) / "api_contract.md"
            contract_path.write_text("dummy", encoding="utf-8")
            openapi_path.write_text("expected-yaml\n", encoding="utf-8")
            ok, message = self.mod.validate_openapi_sync(
                openapi_path=openapi_path,
                contract_path=contract_path,
                generate_openapi_yaml=lambda _: "expected-yaml\n",
            )
            self.assertTrue(ok)
            self.assertEqual(message, "")

    def test_validate_openapi_sync_fails_when_content_drifts(self):
        with tempfile.TemporaryDirectory() as td:
            openapi_path = Path(td) / "openapi.yaml"
            contract_path = Path(td) / "api_contract.md"
            contract_path.write_text("dummy", encoding="utf-8")
            openapi_path.write_text("hand-edited\n", encoding="utf-8")
            ok, message = self.mod.validate_openapi_sync(
                openapi_path=openapi_path,
                contract_path=contract_path,
                generate_openapi_yaml=lambda _: "generated\n",
            )
            self.assertFalse(ok)
            self.assertIn("docs/openapi.yaml is out of sync", message)
            self.assertIn("python scripts/generate_openapi_spec.py", message)


if __name__ == "__main__":
    unittest.main()
