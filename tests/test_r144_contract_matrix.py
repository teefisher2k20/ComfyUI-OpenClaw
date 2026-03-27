import json
import unittest

from services.openapi_generation import parse_api_contract_markdown
from services.request_contracts import get_serializable_contract_bundle


class TestR144ContractMatrix(unittest.TestCase):
    def test_contract_bundle_is_json_serializable(self):
        bundle = get_serializable_contract_bundle()
        encoded = json.dumps(bundle, sort_keys=True)
        self.assertIn("webhook_job_request_v1", encoded)
        self.assertIn("model_manager_import_v1", encoded)

    def test_documented_routes_cover_r144_fixture_paths(self):
        documented = {
            (route.method, route.path) for route in parse_api_contract_markdown()
        }
        bundle = get_serializable_contract_bundle()
        missing = []
        for family in bundle["route_fixtures"].values():
            for route in family:
                key = (route["method"], route["path"])
                if key not in documented:
                    missing.append(key)
        self.assertEqual(missing, [], f"Undocumented R144 route fixtures: {missing}")


if __name__ == "__main__":
    unittest.main()
