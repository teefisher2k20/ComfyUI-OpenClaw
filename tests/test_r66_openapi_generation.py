import tempfile
import unittest
from pathlib import Path


class TestR66OpenApiGeneration(unittest.TestCase):
    def test_parse_api_contract_extracts_assist_stream_routes(self):
        from services.openapi_generation import parse_api_contract_markdown

        routes = parse_api_contract_markdown()
        route_keys = {(r.method, r.path) for r in routes}
        self.assertIn(("POST", "/assist/planner/stream"), route_keys)
        self.assertIn(("POST", "/assist/refiner/stream"), route_keys)
        self.assertIn(("GET", "/events"), route_keys)
        self.assertIn(("GET", "/events/stream"), route_keys)

    def test_generated_yaml_includes_streaming_and_auth_metadata(self):
        from services.openapi_generation import generate_openapi_yaml

        text = generate_openapi_yaml()
        self.assertIn("/assist/planner/stream:", text)
        self.assertIn("x-openclaw-streaming: true", text)
        self.assertIn("OpenClawObservabilityToken", text)
        self.assertIn("/approvals:", text)
        self.assertIn("x-openclaw-auth:", text)
        self.assertIn("OpenClawReasoningRevealHeader", text)
        self.assertIn('name: "debug_reasoning"', text)

    def test_write_openapi_yaml_writes_file(self):
        from services.openapi_generation import write_openapi_yaml

        with tempfile.TemporaryDirectory() as td:
            out = write_openapi_yaml(Path(td) / "openapi.yaml")
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn('openapi: "3.0.3"', content)

    def test_repo_openapi_yaml_is_current(self):
        from services.openapi_generation import generate_openapi_yaml

        repo_file = Path("docs/openapi.yaml")
        self.assertTrue(repo_file.exists(), "docs/openapi.yaml must exist (R66)")
        self.assertEqual(repo_file.read_text(encoding="utf-8"), generate_openapi_yaml())


if __name__ == "__main__":
    unittest.main()
