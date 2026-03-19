import unittest
from unittest.mock import patch

from services.route_bootstrap import _register_bridge_routes
from services.route_bootstrap_contract import load_route_bootstrap_contract


class DummyBridgeHandlers:
    def submit_handler(self, request=None):
        return request

    def deliver_handler(self, request=None):
        return request

    def health_handler(self, request=None):
        return request


class DummyRouter:
    def __init__(self):
        self.calls = []

    def add_post(self, path, handler):
        self.calls.append(("POST", path, handler))

    def add_get(self, path, handler):
        self.calls.append(("GET", path, handler))


class RouteBootstrapContractTests(unittest.TestCase):
    def test_contract_loader_rejects_non_callable_registrar(self):
        def fake_import(_pkg, _rel, _abs, attrs):
            if attrs == ("BridgeHandlers",):
                return (DummyBridgeHandlers,)
            return (object(),)

        with patch(
            "services.route_bootstrap_contract.import_attrs_dual",
            side_effect=fake_import,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                load_route_bootstrap_contract("services")

        self.assertIn("must be callable", str(ctx.exception))

    def test_bridge_route_table_registers_all_aliases(self):
        router = DummyRouter()

        _register_bridge_routes(router, DummyBridgeHandlers())

        paths = [path for _method, path, _handler in router.calls]
        self.assertEqual(len(paths), 12)
        self.assertIn("/openclaw/bridge/submit", paths)
        self.assertIn("/api/openclaw/bridge/health", paths)


if __name__ == "__main__":
    unittest.main()
