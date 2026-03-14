import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "regenerate_openapi_if_needed.py"
    spec = importlib.util.spec_from_file_location("openapi_regen_hook_mod", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load regenerate_openapi_if_needed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestOpenApiRegenHook(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_regenerate_openapi_if_needed_noop_when_already_synced(self):
        guard = SimpleNamespace(
            validate_openapi_sync=lambda **_: (True, ""),
        )
        with tempfile.TemporaryDirectory() as td:
            openapi_path = Path(td) / "openapi.yaml"
            contract_path = Path(td) / "api_contract.md"
            openapi_path.write_text("expected\n", encoding="utf-8")
            contract_path.write_text("dummy", encoding="utf-8")
            changed, message = self.mod.regenerate_openapi_if_needed(
                openapi_path=openapi_path,
                contract_path=contract_path,
                guard_module=guard,
                write_openapi_yaml=lambda *_args, **_kwargs: self.fail(
                    "writer should not run when spec is already synced"
                ),
            )
            self.assertFalse(changed)
            self.assertEqual(message, "")
            self.assertEqual(openapi_path.read_text(encoding="utf-8"), "expected\n")

    def test_regenerate_openapi_if_needed_rewrites_drifted_spec(self):
        guard = SimpleNamespace(
            validate_openapi_sync=lambda **_: (False, "drift"),
        )

        def writer(out_path, *, contract_path):
            Path(out_path).write_text("generated\n", encoding="utf-8")
            self.assertTrue(Path(contract_path).exists())
            return Path(out_path)

        with tempfile.TemporaryDirectory() as td:
            openapi_path = Path(td) / "openapi.yaml"
            contract_path = Path(td) / "api_contract.md"
            openapi_path.write_text("hand-edited\n", encoding="utf-8")
            contract_path.write_text("dummy", encoding="utf-8")
            changed, message = self.mod.regenerate_openapi_if_needed(
                openapi_path=openapi_path,
                contract_path=contract_path,
                guard_module=guard,
                write_openapi_yaml=writer,
            )
            self.assertTrue(changed)
            self.assertIn("Regenerated generated spec", message)
            self.assertEqual(openapi_path.read_text(encoding="utf-8"), "generated\n")


if __name__ == "__main__":
    unittest.main()
